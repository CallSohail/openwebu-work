"""How Chat Inspector's token-count tuning was made. Rerun it to retune after tokenizers change.

    pip install tokenizers numpy
    # in the folder you run it from:
    #   lang_valid.csv, lang_test.csv  from https://huggingface.co/datasets/papluca/language-identification
    #   corpus.json                     optional extra samples ({"name": "text"}); may be {}
    #   Xenova_gpt-4o.json, Xenova_gpt-4.json, Xenova_Meta-Llama-3.1-Tokenizer.json,
    #   Qwen_Qwen2.5-7B-Instruct.json, mistralai_Mistral-7B-Instruct-v0.3.json
    #                                   tokenizer.json files from those Hugging Face repositories
    python fit_tokens.py

It fits one coefficient per character class on half of the passages (20
languages from the dataset, plus code and documentation from this repository)
and reports the error on the other half. The per-family coefficients it prints
are the TUNING table in chat_inspector.py, in the same order. It also reports
which family's tuning has the smallest worst case, used for unknown models.
"""
import csv, json, re, glob, random, os, numpy as np
from collections import defaultdict
from tokenizers import Tokenizer
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
random.seed(7)
def lang_docs(path, per=12):
    rows=defaultdict(list)
    for r in csv.DictReader(open(path,encoding='utf-8')): rows[r['labels']].append(r['text'])
    docs=[]
    for lang,texts in rows.items():
        for i in range(0,len(texts),per): docs.append(('lang_'+lang,'\n'.join(texts[i:i+per])))
    return docs
code=[]
for f in sorted(glob.glob(REPO+'/**/*.py',recursive=True)+glob.glob(REPO+'/**/*.mjs',recursive=True)+glob.glob(REPO+'/**/*.md',recursive=True)+glob.glob(REPO+'/**/*.json',recursive=True)):
    if '/.git/' in f or 'node_modules' in f: continue
    s=open(f,encoding='utf-8',errors='ignore').read()
    kind='code_'+f.rsplit('.',1)[1]
    for i in range(0,min(len(s),60000),2500): 
        if len(s[i:i+2500])>500: code.append((kind,s[i:i+2500]))
random.shuffle(code)
mine=json.load(open('corpus.json',encoding='utf-8')) if os.path.exists('corpus.json') else {}
extra=[('mine_'+k,v) for k,v in mine.items() if k in ('ko','he','numbers','urls','emoji_chat','markdown_table','indented_code','zh','ja')]
train=lang_docs('lang_valid.csv')+code[::2]+extra
test=lang_docs('lang_test.csv')+code[1::2]+[(k,v[len(v)//2:]+v[:len(v)//2]) for k,v in extra]
files=[('o200k','Xenova_gpt-4o.json'),('cl100k','Xenova_gpt-4.json'),('llama','Xenova_Meta-Llama-3.1-Tokenizer.json'),('qwen','Qwen_Qwen2.5-7B-Instruct.json'),('mistral','mistralai_Mistral-7B-Instruct-v0.3.json')]
toks={n:Tokenizer.from_file(f) for n,f in files}
PATS=[('digit',r'\d'),('punct',r'[!-/:-@\[-`{-~]'),('newline',r'\n'),('space2',r'(?<= ) +'),('latx',r'[À-ɏ]'),('viet',r'[Ḁ-ỿ]'),
 ('cjk',r'[぀-ヿ㐀-䶿一-鿿豈-﫿　-〿＀-￯]'),('hangul',r'[가-힯]'),('greek',r'[Ͱ-Ͽ]'),('cyr',r'[Ѐ-ԯ]'),
 ('hebrew',r'[֐-׿]'),('arabic',r'[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-ﻼ]'),('deva',r'[ऀ-ॿ]'),('thai',r'[฀-๿]'),('emoji',r'[\U0001F000-\U0001FFFF☀-➿]')]
def feats(s):
    words=re.findall(r'[A-Za-z]+',s)
    return np.array([len(words),sum(max(0,len(w)-6) for w in words)]+[len(re.findall(p,s)) for _,p in PATS],float)
labels=['word','longword']+[n for n,_ in PATS]
def mats(docs):
    X=np.array([feats(s) for _,s in docs]); Y={m:np.array([len(t.encode(s,add_special_tokens=False).ids) for _,s in docs],float) for m,t in toks.items()}
    return X,Y
Xtr,Ytr=mats(train); Xte,Yte=mats(test)
def fit(X,Y,prior=None,lam=0.0):
    A=X/Y[:,None]; b=np.ones(len(Y))
    if prior is not None: A=np.vstack([A,lam*np.eye(A.shape[1])]); b=np.concatenate([b,lam*prior])
    c,*_=np.linalg.lstsq(A,b,rcond=None); return np.clip(c,0,None)
avg_tr=np.mean(list(Ytr.values()),axis=0)
G=fit(Xtr,avg_tr)
F={m:fit(Xtr,Ytr[m],G,0.5) for m in toks}
kinds=sorted(set(k for k,_ in test))
def score(pred,y): e=np.abs(pred/y-1); return e.mean()*100, np.percentile(e,90)*100
print(f"{len(train)} training passages, {len(test)} test passages")
for m in toks:
    y=Yte[m]; c4=np.array([max(1,len(s)//4) for _,s in test])
    a=score(c4,y); b=score(Xte@G,y); c=score(Xte@F[m],y)
    print(f"{m:8} chars/4 mean {a[0]:5.1f}% p90 {a[1]:5.1f}% | shared mean {b[0]:5.1f}% p90 {b[1]:5.1f}% | family mean {c[0]:5.1f}% p90 {c[1]:5.1f}%")
print('\nworst kinds for the shared fit (mean over models):')
rows=[]
for k in kinds:
    idx=[i for i,(kk,_) in enumerate(test) if kk==k]
    errs=[np.mean(np.abs(Xte[idx]@G/Yte[m][idx]-1))*100 for m in toks]; c4=[np.mean(np.abs(np.array([len(test[i][1])//4 for i in idx])/Yte[m][idx]-1))*100 for m in toks]
    rows.append((np.mean(errs),k,np.mean(c4)))
for e,k,c in sorted(rows,reverse=True)[:10]: print(f"  {k:16} shared {e:5.1f}%   chars/4 {c:5.1f}%")
json.dump({'labels':labels,'patterns':PATS,'shared':G.round(4).tolist(),'families':{m:c.round(4).tolist() for m,c in F.items()}},open('coef3.json','w'),indent=1)

print('\nas the estimator for an unknown model (mean error against each tokenizer):')
best=None
for name,c in list(F.items())+[('shared',G)]:
    errs=[np.mean(np.abs(Xte@c/Yte[m]-1))*100 for m in toks]
    print(f"  using {name:8}: " + ' '.join(f"{m} {e:5.1f}%" for m,e in zip(toks,errs)) + f"   worst {max(errs):5.1f}%")
    if best is None or max(errs)<best[0]: best=(max(errs),name)
print('pick for unknown models:', best)
