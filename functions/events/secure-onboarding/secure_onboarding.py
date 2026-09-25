"""
title: Secure Dynamic Onboarding Rich UI
author: CallSohail
author_url: https://github.com/CallSohail/openwebu-work
funding_url: https://github.com/CallSohail/openwebu-work
version: 9.2.2
required_open_webui_version: 0.11.3
description: Multilingual, role-aware interactive onboarding guide and tutorial for Open WebUI. Delivered once per user (new sign-ups, first login, or pushed to everyone), updated in place when content or permissions change, and never shows a feature the user is not allowed to use.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import re
import time
import uuid
from typing import Any, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field


log = logging.getLogger("openwebui.secure_onboarding")
log.setLevel(logging.INFO)

ONBOARDING_VERSION = 5
TEMPLATE_REVISION = 11
SETTINGS_KEY = "secure_onboarding"
TEST_SETTINGS_KEY = "secure_onboarding_test"

# BEGIN GENERATED LOCALE DATA
SUPPORTED_LOCALES = ('fr', 'en', 'ca', 'es')
LOCALE_NAMES = {"ca": "Català", "en": "English", "es": "Español", "fr": "Français"}
EXTRA_LOCALE_TRANSLATIONS = {"ca": {" Le nombre, visible par les administrateurs, indique les utilisateurs actifs.\u001f The number, shown to administrators, is the count of active users.": " El nombre, mostrat als administradors, és el compta d'usuaris actius.", " résume les décisions de ce fil en 3 points.\u001f summarize the decisions in this thread in 3 points.": " resumeix les decisions d'aquest fil en 3 punts.", " sur \u001f of ": " de ", "+ › Attach Knowledge › choisissez la base. Raccourci : tapez # dans la zone de saisie.\u001f+ › Attach Knowledge › pick the collection. Shortcut: type # in the message box.": "+ › Adjunta Coneixement › tria la col·lecció. Accés ràpid: escriu # al camp de missatge.", "+ › Attach Webpage.\u001f+ › Attach Webpage.": "+ › Adjanta Pàgina Web.", "+ › Reference Chats › choisissez la conversation.\u001f+ › Reference Chats › pick the chat.": "+ › Xats de referència › tria el xat.", "1 = inutilisable, 5 = moyen.\u001f1 = unusable, 5 = average.": "1 = inutilitzable, 5 = mitjà.", "2 réponses\u001f2 replies": "2 respostes", "Accueil\u001fHome": "Inici", "Action ajoutée par votre organisation.\u001fAction added by your organization.": "Acció afegida per la teva organització.", "Action sous les réponses\u001fAction under answers": "Acció sota les respostes", "Activer des outils et fonctions pour cette conversation, comme la recherche web.\u001fTurn on tools and features for this chat, such as web search.": "Activa eines i funcionalitats per a aquesta conversa, com la Cerca web.", "Activez la génération d’images.\u001fTurn on image generation.": "Activa generació d'imatges.", "Activez-le avant d’envoyer. Il reste actif pour la conversation.\u001fTurn it on before sending. It stays on for the conversation.": "Activa-la abans d'enviar. Es manté activa durant la conversa.", "Activé pour cette conversation seulement. Désactivez-le quand vous n’en avez plus besoin.\u001fOn for this conversation only. Turn it off when you no longer need it.": "Actiu nomé per a aquesta conversa. Desactiva-lo quan ja no el necessitis.", "Adapté à votre compte\u001fTailored to your account": "Ajustat al teu compte", "Admin Panel › Users › Groups pour les permissions.\u001fAdmin Panel › Users › Groups for permissions.": "Panel d'administració › Usuaris › Grups per a permisos.", "Administrateurs\u001fAdministrators": "Administradors", "Administration\u001fAdministration": "Administració", "Affiche la note dans la barre latérale pour la retrouver vite et la glisser dans une conversation.\u001fShows the note in the sidebar to find it fast and drag it into a chat.": "Mostra la nota a la barra lateral per trobar-la ràpidament i arrossegar-la a un xat.", "Affiche vos automatisations prévues et passées. En lecture seule : cliquez un événement pour ouvrir l’automatisation ou le chat produit.\u001fShows your planned and past automations. Read-only: click an event to open the automation or the chat it produced.": "Mostra les teves automatitzacions planificades i passades. Només lectura: clica un esdeveniment per obrir l'automatització o el xat que va produir.", "Agenda personnel avec vues mois/semaine/jour, événements récurrents, rappels et partage.\u001fPersonal calendar with month/week/day views, recurring events, reminders and sharing.": "Calendari personal amb vistes de mes/setmana/dia, esdeveniments periòdics, recordatoris i compartició.", "Ajoute le texte d’une page à partir de son adresse.\u001fAdds a page’s text from its address.": "Afegeix el text d'una pàgina a partir de la seva adreça.", "Ajoute une conversation passée comme contexte.\u001fAdds a past chat as context.": "Afegeix un xat anterior com a context.", "Ajouter du contexte\u001fAdd context": "Afegeix context", "Ajouter du contexte : fichiers, capture, page web, notes, documents, anciennes conversations.\u001fAdd context: files, capture, web page, notes, documents, past chats.": "Afegeix context: fitxers, captura, pàgina web, notes, documents, xats anteriors.", "Ajoutez un System Prompt et des documents.\u001fAdd a System Prompt and documents.": "Afegiu un prompt del sistema i documents.", "Ajoutez un emoji et un message court (« en réunion », « en cours »), visible par les autres dans les canaux.\u001fSet an emoji and a short message (“in a meeting”, “in class”), shown to others in channels.": "Configura un emoji i un missatge curt ('en una reunió', 'a classe') que es mostren a altres en els canals.", "Ajoutez un second modèle à côté du sélecteur.\u001fAdd a second model next to the selector.": "Afegeix un segon model al costat del selector.", "Ajoutez une phrase précise, puis Save.\u001fAdd one precise sentence, then Save.": "Afegeix una frase precisa i després Desa.", "Ajoutez, corrigez ou supprimez ce qui est retenu.\u001fAdd, edit or delete what is remembered.": "Afegiu, editi o suprimiteu el què s'ha recordat.", "Alerte avant l’événement (10 minutes par défaut) : notification dans la plateforme et dans le navigateur si vous l’avez autorisé.\u001fAlert before the event (10 minutes by default): in-app notification, and browser notification if allowed.": "Alerta abans de l'esdeveniment (10 minuts per defecte): notificació a la app, i notificació al navegador si està permès.", "All day\u001fAll day": "Tot el dia", "Aller rapidement à une date.\u001fJump quickly to a date.": "Salta ràpidament a una data.", "Améliorer, résumer, extraire les actions, réécrire la sélection : l’assistant modifie la note directement.\u001fEnhance, summarize, extract action items, rewrite selection: the assistant edits the note directly.": "Millora, resumeix, extreu tasques d'acció, reescriu la selecció: l'assistent edita directament la nota.", "Analysez vos documents : résumé, extraction, traduction, comparaison.\u001fAnalyze your documents: summary, extraction, translation, comparison.": "Analitza els teus documents: resum, extracció, traducció, comparació.", "Annuler / rétablir\u001fUndo / redo": "Desfer / Referir", "Après 👍, seules les notes hautes sont disponibles. 10 = parfaite.\u001fAfter 👍, only high scores are available. 10 = perfect.": "Després de 👍, nomé es disponibles puntuacions altes. 10 = perfecte.", "Assistant actif\u001fActive assistant": "Assistent actiu", "Assistants\u001fAssistants": "Assistents", "Astuce : maintenez Maj (Shift) dans ce menu pour épingler Calendar ou Automations dans la barre latérale.\u001fTip: hold Shift in this menu to pin Calendar or Automations to the sidebar.": "Consell: mantén Shift en aquest menú per fixar Calendari o Automatitzacions a la barra lateral.", "Attendez la fin du chargement.\u001fWait for the upload to finish.": "Espera que la pujada s'acabi.", "Aucune synchronisation avec Outlook ou Google Agenda.\u001fNo sync with Outlook or Google Calendar.": "Sense sincronització amb Outlook o Google Calendar.", "Automations › Create.\u001fAutomations › Create.": "Crear automatitzacions.", "Automatisations\u001fAutomations": "Automatitzacions", "Avant chaque action, l’assistant affiche ce qu’il veut faire et attend votre accord. Recommandé pour tout ce qui envoie, modifie ou supprime.\u001fBefore each action, the assistant shows what it wants to do and waits for your approval. Recommended for anything that sends, changes or deletes.": "Abans de cada acció, l'assistent mostra el què vol fer i espera la teva aprovació. Recomanat per a tot allò que envia, canvia o suprimeix.", "Avantages et limites\u001fPros and cons": "Pros i contres", "Avis\u001fFeedback": "Retroalimentació", "Avis peu utile\u001fUnhelpful feedback": "Retroalimentació poc útila", "Avis sur les réponses\u001fRating answers": "Valorant respostes", "Avis utile\u001fHelpful feedback": "Retroalimentació útil", "Barre latérale\u001fSidebar": "Barra lateral", "Barre latérale › Notes. Un espace pour rédiger des textes qui durent : comptes rendus, plans, idées.\u001fSidebar › Notes. A place for lasting writing: minutes, outlines, ideas.": "Barra lateral › Notes. Un lloc per a escriura duradure: resums, esbossos, idees.", "Bases de documents\u001fKnowledge": "Coneixement", "Bienvenue ! Ce guide interactif vous apprend à utiliser la plateforme, écran par écran. Il montre uniquement les fonctions disponibles pour votre compte.\u001fWelcome! This interactive guide teaches you how to use the platform, screen by screen. It only shows the features available on your account.": "Benvingut! Aquesta guia interactiva t'ensenya com utilitzar la plataforma, pantalla per pantalla. Només mostra les funcionalitats disponibles al teu compte.", "Bienvenue sur \u001fWelcome to ": "Benvingut/ a ", "Bon usage\u001fGood practice": "Bones pràctiques", "Bonne réponse\u001fGood response": "Resposta bona", "Bouton rond (zone vide) : conversation orale.\u001fRound button (empty box): spoken conversation.": "Botó rodó (caixa buida): conversa parlada.", "Calcule la moyenne et l’écart-type par groupe et trace un histogramme.\u001fCompute mean and standard deviation per group and plot a histogram.": "Calcula la mitjana i la desviació estàndard per grup i dibuixa un histograma.", "Calculs, données, graphiques\u001fCalculations, data, charts": "Càlculs, dades, gràfics", "Calendrier\u001fCalendar": "Calendari", "Canaux\u001fChannels": "Canals", "Ce guide vous montre l’interface réelle, élément par élément. Cliquez sur un élément de l’écran ou sur son explication pour le mettre en évidence.\u001fThis guide walks you through the real interface, one element at a time. Click any element on the screen or its explanation to highlight it.": "Aquesta guia et porta a través de la interfície real, un element cada vegada. Clica qualsevol element a la pantalla o la seva explicació per ressaltar-lo.", "Ce que l’assistant fait seul\u001fWhat the assistant does on its own": "El què fa l'assistent sols", "Ce que vous allez apprendre\u001fWhat you will learn": "Què aprendràs", "Ces outils ne fonctionnent qu’avec les assistants configurés pour eux et selon vos permissions. Si rien ne se passe, choisissez un autre assistant.\u001fThese tools only work with assistants set up for them and within your permissions. If nothing happens, choose another assistant.": "Aquestes eines només funcionen amb assistents configurats per a elles i dins dels teus permisos. Si res no passa, tria'n un altre assistent.", "Cette ligne montre l’outil lancé. Cliquez dessus pour voir ce qui a été envoyé et reçu.\u001fThis line shows the tool that ran. Click it to see what was sent and received.": "Aquesta línia mostra l'eina que s'ha executat. Clica-la per veure què s'ha enviat i rebut.", "Cette liste s’adapte à votre compte. Ouvrez une fiche pour savoir où la trouver et comment bien l’utiliser.\u001fThis list adapts to your account. Open a card to see where to find it and how to use it well.": "Aquesta llista s'adapt al teu compte. Obre una targeta per veure on trobar-la i com utilitzar-la bé.", "Change la vue : mois, semaine ou jour.\u001fSwitch view: month, week or day.": "Altera la vista: mes, setmana o dia.", "Changez d’assistant pour voir d’autres suggestions.\u001fSwitch assistants to see other suggestions.": "Alterna assistents per veure altres suggerències.", "Chapitres\u001fChapters": "Capítols", "Chaque exécution avec son statut (success ou error) et le lien vers la conversation créée.\u001fEach run with its status (success or error) and a link to the chat created.": "Cada execució amb la seva situació (èxit o error) i un enllaç al xat creat.", "Chaque jour à 18:00\u001fEvery day at 18:00": "Diari a les 18:00", "Chaque lundi à 08:00 · prochaine : 21 sept.\u001fEvery Monday at 08:00 · next: 21 Sep": "Dilluns cada setmana a les 08:00 · següent: 21 Set", "Chaque lundi, recherche les nouveautés publiées sur ce sujet et présente-les dans un tableau avec lien, source et date.\u001fEvery Monday, find what was published on this topic and list it in a table with link, source and date.": "Diumenge cada setmana, troba què s'ha publicat sobre aquest tema i el llista en una taula amb enllaç, font i data.", "Cherche dans les canaux dont vous êtes membre.\u001fSearches channels you belong to.": "Busca als canals als quals pertanys.", "Cherche et lit des pages quand Web Search est activé.\u001fSearches and reads pages when Web Search is on.": "Cerqua i llegeix pàgines quan la Cerca web està activada.", "Cherche sur Internet et cite ses sources. À activer pour toute information récente : actualités, dates, règlements.\u001fSearches the internet and cites sources. Turn on for anything recent: news, dates, regulations.": "Busca a Internet i cita fonts. Engega per tot allò recent: notícies, dates, reglaments.", "Choisir le bon assistant\u001fChoosing the right assistant": "Elixe el bon assitent", "Choisir l’assistant\u001fChoosing the assistant": "Triant l'assistent", "Choisir l’assistant ou le modèle qui répond.\u001fChoose the assistant or model that answers.": "Tria l'assistent o el model que respon.", "Choisissez ce qui était vraiment bien : exactitude, respect des consignes, clarté…\u001fPick what was actually good: accuracy, following instructions, clarity…": "Tria el què realment va bé: precisió, seguiment d'instruccions, claredat…", "Choisissez la commande et complétez les champs.\u001fPick the command and fill in the fields.": "Tria la comanda i omple els camps.", "Choisissez un modèle, un prompt système et envoyez.\u001fPick a model, a system prompt and send.": "Tria un model, un prompt del sistema i envia.", "Choisissez une note et une raison.\u001fPick a score and a reason.": "Tria una puntuació i una raó.", "Clair et professionnel\u001fClear and professional": "Clar i professional", "Classez l’avis par sujet, par exemple « recherche » ou « rédaction ».\u001fCategorize by topic, e.g. “search” or “writing”.": "Categoritza per tema, p. ex. 'cerca' o 'escritura'.", "Cliquez pour le modifier ou le supprimer.\u001fClick to edit or delete it.": "Clica per editar o suprimir-la.", "Cliquez pour l’appliquer à cette réponse.\u001fClick to apply it to this answer.": "Clica per aplicar-ho a aquesta resposta.", "Cliquez pour ouvrir. ⋯ pour les options.\u001fClick to open. ⋯ for options.": "Clica per obrir. ⋯ pels opcions.", "Cliquez pour renommer la note.\u001fClick to rename the note.": "Clica per reanomenar la nota.", "Cliquez sur + pour créer un dossier. Glissez-déposez ensuite des conversations dedans.\u001fClick + to create a folder. Then drag chats into it.": "Clica + per crear un dossier. Després arrossega els xats hi dentro.", "Cliquez sur + pour ouvrir ce menu. Les lignes avec une flèche › ouvrent une seconde liste : cliquez dessus ici pour la voir.\u001fClick + to open this menu. Rows with an arrow › open a second list: click them here to see it.": "Clica + per obrir aquest menú. Les files amb una fletxa › obren una segona llista: cliques aquí per veure'les.", "Cliquez sur + › Upload Files, ou glissez le fichier dans la zone de saisie.\u001fClick + › Upload Files, or drag the file into the message box.": "Clica + › Carrega fitxers, o arrossega el fitxer al camp de missatge.", "Cliquez sur le nom à droite de la zone de saisie pour ouvrir la liste.\u001fClick the name on the right of the message box to open the list.": "Clica el nom a la dreta del camp de missatge per obrir la llista.", "Cliquez sur l’icône de l’action.\u001fClick the action icon.": "Clica l'icona d'acció.", "Cliquez sur l’écran ou sur une explication\u001fClick the screen or an explanation": "Clica la pantalla o una explicació", "Cliquez sur une colonne pour trier.\u001fClick a column to sort.": "Clica una columna per ordenar.", "Cliquez sur une suggestion : elle remplit la zone de saisie. Complétez-la avec vos détails avant d’envoyer.\u001fClick a suggestion: it fills the message box. Add your details before sending.": "Clica una suggerència: omplí el camp de missatge. Afegeix els teus detalls abans d'enviar-la.", "Cliquez sur votre nom en bas de la barre latérale pour ouvrir ce menu. Le point vert indique que vous êtes en ligne.\u001fClick your name at the bottom of the sidebar to open this menu. The green dot means you are online.": "Clica el teu nom a la part inferior de la barra lateral per obrir aquest menú. El punet verd significa que estàs en línia.", "Cliquez sur votre nom en bas de la barre latérale.\u001fClick your name at the bottom of the sidebar.": "Clica el teu nom a la part inferior de la barra lateral.", "Cliquez sur votre nom.\u001fClick your name.": "Clica el teu nom.", "Cliquez 👍 ou 👎.\u001fClick 👍 or 👎.": "Clica 👍 o 👎.", "Coller l’adresse d’une page web : son texte est lu et ajouté comme contexte.\u001fPaste a web page address: its text is read and added as context.": "Enganxa l'adreça d'una pàgina web: es llegiran el text i s'hi afegirà com a context.", "Collez l’adresse complète (https://…).\u001fPaste the full address (https://…).": "Enganxa l'adreça completa (https://…)", "Commencer\u001fGet started": "Comença", "Commencer la visite\u001fStart the tour": "Comença la visita guiada", "Comment l’utiliser\u001fHow to use it": "Com fer servir-la", "Compare ces deux documents et liste les différences dans un tableau.\u001fCompare these two documents and list the differences in a table.": "Compara aquests dos documents i llista les diferències en una taula.", "Comparer des modèles\u001fCompare models": "Compara models", "Comparer deux options\u001fCompare two options": "Comparar dues opcions", "Compte rendu de réunion\u001fMeeting minutes": "Acta de reunió", "Compteur\u001fCounter": "Comptador", "Confidentialité\u001fPrivacy": "Privadesa", "Connaît la date et calcule des délais : « dans 3 semaines », « lundi prochain ».\u001fKnows the date and computes delays: “in 3 weeks”, “next Monday”.": "Sabe la data i calcula delais: 'en 3 setmanes', 'dilluns proper'.", "Consignes appliquées à toutes les conversations du dossier : rôle, ton, format. Visible seulement si votre compte l’autorise.\u001fInstructions applied to every chat in the folder: role, tone, format. Only shown if your account allows it.": "Instruccions aplicades a cada xat del dossier: paper, to, format. Només visible si el teu compte ho permet.", "Continuer\u001fContinue": "Continua", "Conversation\u001fConversation": "Conversa", "Conversations\u001fChats": "Xats", "Copie la réponse avec sa mise en forme.\u001fCopies the answer with its formatting.": "Copia la resposta amb el seu formatat.", "Copier\u001fCopy": "Copiar", "Copier un lien ou partager la note.\u001fCopy a link or share the note.": "Copia un enllaç o compartiu la nota.", "Copier une réponse sans la relire\u001fCopying an answer without reading it": "Copiant una resposta sense llegir-la", "Corrige le texte de la réponse, par exemple avant de la copier.\u001fEdit the answer text, for example before copying it.": "Edita el text de la resposta, per exemple abans de copiar-la.", "Crée le dossier. Vous pourrez modifier ces réglages plus tard.\u001fCreates the folder. You can change these settings later.": "Crea el dossier. Pots canviar aquestes configuracions més tard.", "Crée un événement. Vous pouvez aussi cliquer directement sur un jour de la grille.\u001fCreates an event. You can also click a day on the grid.": "Crea un esdeveniment. També pots clicar un dia a la graella.", "Crée une image à partir de votre description.\u001fCreates an image from your description.": "Crea una imatge a partir de la teva descripció.", "Crée une image à partir d’une description.\u001fCreates an image from a description.": "Crea una imatge a partir d'una descripció.", "Crée une note. La flèche propose d’autres options de création.\u001fCreates a note. The arrow offers other creation options.": "Crea una nota. La fletxa ofereix altres opcions de creació.", "Crée, déplace ou supprime des événements et consulte votre semaine.\u001fCreates, moves or deletes events and checks your week.": "Crea, mou o suprim esdeveniments i comprova la teva setmana.", "Crée, liste, met en pause ou supprime une tâche planifiée.\u001fCreates, lists, pauses or deletes a scheduled task.": "Crea, llista, pausa o suprimeix una tasca programada.", "Créer et gérer assistants, bases de documents, modèles de demandes, selon vos droits.\u001fCreate and manage assistants, document collections and prompt templates, depending on your rights.": "Crea i gestiona assistents, col·leccions de documents i plantilles d'avisos, segons els teus drets.", "Créer et gérer des assistants, documents ou modèles de demandes, selon vos droits.\u001fCreate and manage assistants, documents or prompt templates, depending on your rights.": "Crea i gestiona assistents, documents o plantilles d'avísos, segons els teus drets.", "Créer et suivre une automatisation\u001fCreating and monitoring an automation": "Creació i control d'una automatització", "Créer un canal (selon vos droits) et choisir qui y a accès.\u001fCreate a channel (if allowed) and choose who can access it.": "Crea un canal (si està permès) i tria qui pot accedir-hi.", "Créer un dossier pas à pas\u001fCreate a folder step by step": "Crea un dossier pas a pas", "Créer un événement\u001fCreating an event": "Creant un esdeveniment", "Créer une illustration\u001fCreate an illustration": "Crea il·lustració", "Créez d’autres calendriers (« Équipe », « Projet ») avec leur couleur.\u001fCreate more calendars (“Team”, “Project”) with their own colour.": "Crea més calendaris ('Equip', 'Projecte') amb la seva pròpia color.", "Créé automatiquement : votre calendrier par défaut. Cliquez pour l’afficher ou le masquer.\u001fCreated automatically: your default calendar. Click to show or hide it.": "Creat automàticament: el teu calendari per defecte. Clica per mostrar-lo o amagar-lo.", "C’est noté : événement créé vendredi à 15:00, rappel 10 minutes avant.\u001fDone: event created on Friday at 15:00 with a reminder 10 minutes before.": "Fet: esdeveniment creat el divendres a les 15:00 amb un recordatori 10 minuts abans.", "Dans + › Tool Permissions, décidez du niveau de contrôle quand l’assistant utilise un outil.\u001fIn + › Tool Permissions, decide how much control you keep when the assistant uses a tool.": "A + › Permisos d'eines, decideix quants controls mantens quan l'assistent usa una eina.", "Dans la conversation\u001fIn the chat": "Dins el xat", "Dans quel calendrier ranger l’événement.\u001fWhich calendar to put it in.": "Quin calendari posar-hi.", "Date de modification, nombre de mots et de caractères.\u001fLast edit, word and character count.": "Última edició, comptador de paraules i caràcters.", "Date et heure\u001fDate & time": "Data i hora", "Date et heure, recherche web, documents, mémoire, notes, anciennes conversations, calendrier, automatisations, images, code, listes de tâches : selon l’assistant et vos droits.\u001fDate & time, web search, documents, memory, notes, past chats, calendar, automations, images, code, task lists: depending on the assistant and your rights.": "Data i hora, cerca web, documents, memòria, notes, xats anteriors, calendari, automatitzacions, imatges, codi, llistes de tasques: depèn de l'assistent i els teus drets.", "Date et heures, ou journée entière.\u001fDate and times, or all day.": "Data i hores, o tot el dia.", "Demandes planifiées\u001fScheduled requests": "Petites programades", "Demandez ce que vous voulez en citant le fichier.\u001fAsk what you need, referring to the file.": "Demanda el què necessites, referint-te al fitxer.", "Demandez de continuer ou de réutiliser ce qui a été produit.\u001fAsk to continue or reuse what was produced.": "Demanda continuar o reutilitzar el què s'havia produït.", "Demandez en langage naturel.\u001fAsk in plain language.": "Demanda en llenguatge simple.", "Demandez les sources utilisées.\u001fAsk for the sources used.": "Demana les fonts usades.", "Des demandes qui s’exécutent toutes seules à heure fixe. Voir chapitre Automatisations.\u001fRequests that run on their own on a schedule. See the Automations chapter.": "Peticions que s'executen sols segons un horari. Veure el capítol Automatitzacions.", "Des documents persistants avec un assistant qui peut les modifier directement.\u001fLasting documents with an assistant that can edit them directly.": "Documents duradors amb un assistent que els pot editar directament.", "Des idées de questions prêtes à l’emploi. Cliquez sur l’une d’elles pour la placer dans la zone de saisie.\u001fReady-made question ideas. Click one to place it in the message box.": "Idees de preguntes prèstes. Clica'n una per posar-la al camp de missatge.", "Des outils connectés permettent à l’assistant de consulter ou agir sur des services.\u001fConnected tools let the assistant look up or act on services.": "Les eines connectades permeten a l'assistent cercar o actuar sobre serveis.", "Dicter, écouter, parler\u001fDictate, listen, talk": "Dicta, escolta, parla", "Dictez ou enregistrez de l’audio : le texte est ajouté à la note.\u001fDictate or record audio: the text is added to the note.": "Dicta o grava àudio: el text s'afegeix a la nota.", "Dictée\u001fDictation": "Dictat", "Dictée avec le micro, mode vocal avec le bouton rond, lecture des réponses avec l’icône haut-parleur.\u001fDictation with the mic, voice mode with the round button, read-aloud with the speaker icon.": "Dictat amb micròfon, mode veu amb el botó rodó, lectura en veu alta amb l'icona d'altaveu.", "Discussions d’équipe avec l’IA\u001fTeam discussions with AI": "Discussions d'equip amb IA", "Documents\u001fKnowledge": "Coneixement", "Documents confidentiels non autorisés\u001fUnauthorized confidential documents": "Documents confidencials sense autorització", "Donner un avis positif\u001fGiving positive feedback": "Donant retroalimentació positiva", "Donner votre avis\u001fGive feedback": "Fes retroalimentació", "Donner votre avis sur les réponses\u001fRate the answers": "Valora les respostes", "Données personnelles de tiers\u001fOther people’s personal data": "Dades personals d'altres persones", "Dossiers\u001fFolders": "Dossiers", "Déconnectez-vous sur un ordinateur partagé.\u001fSign out on a shared computer.": "Tanca sessió en un ordinador compartit.", "Déconnexion. Indispensable sur un ordinateur partagé.\u001fSigns you out. Essential on a shared computer.": "Tanca la teva sessió. És essencial en un ordinador compartit.", "Décrivez le calcul ou le graphique attendu.\u001fDescribe the calculation or chart you want.": "Descriu el càlcul o gràfic que vols.", "Décrivez sujet, style, cadrage et format.\u001fDescribe subject, style, framing and format.": "Descriu el tema, estil, marcament i format.", "Définit si l’assistant peut utiliser les outils librement (Full access) ou doit vous demander avant chaque action (Ask for approval). La flèche ouvre ce choix.\u001fSets whether the assistant can use tools freely (Full access) or must ask before each action (Ask for approval). The arrow opens this choice.": "Configura si l'assistent pot usar eines lliurement (Accés total) o ha de demanar abans de cada acció (Demana aprovació). La fletxa obre aquesta tria.", "Démarre une conversation vide. Changez de conversation quand vous changez de sujet.\u001fStarts an empty chat. Start a new one when you change topic.": "Inicia un xat buit. Inicita'n un de nou quan canvies de tema.", "Démarrer\u001fStart": "Comença", "Démarrez vos conversations depuis le dossier.\u001fStart chats from the folder.": "Inicia xats des del dossier.", "Désactivez-la quand l’information récente n’est pas nécessaire.\u001fTurn it off when recent information is not needed.": "Desactiva-la quan no necessitis informació recent.", "Détails\u001fDetails": "Detalls", "Détails de génération : durée, longueur.\u001fGeneration details: time, length.": "Detalls generació: temps, longitud.", "E-mail au fournisseur\u001fEmail to supplier": "Correu electrònic al proveïdor", "En 5 points\u001fIn 5 points": "En 5 punts", "En cas de doute, choisissez Ask for approval : vous gardez la main sur chaque action.\u001fIf in doubt, choose Ask for approval: you stay in control of every action.": "En cas de dubte, tria Demana aprovació: mantens el control de cada acció.", "En pause\u001fPaused": "En pausa", "En violet : une automatisation planifiée.\u001fIn purple: a scheduled automation.": "En porpra: una automatització programada.", "Enregistre l’avis. Il n’est pas visible par les autres utilisateurs.\u001fSaves the rating. It is not visible to other users.": "Desa la puntuació. No és visible per altres usuaris.", "Enregistre l’événement.\u001fSaves the event.": "Desa l'esdeveniment.", "Enregistrer\u001fRecord": "Grava", "Enregistrez, puis reformulez ou régénérez pour obtenir une meilleure réponse.\u001fSave, then rephrase or regenerate for a better answer.": "Desa, després reformula o regenera per una resposta millor.", "Environ 10 minutes\u001fAbout 10 minutes": "Uns 10 minuts", "Envoyer / mode vocal\u001fSend / voice mode": "Enviar / mode veu", "Envoyer un fichier depuis votre ordinateur : PDF, Word, Excel, image. Vous pouvez aussi le glisser dans la zone de saisie.\u001fSend a file from your computer: PDF, Word, Excel, image. You can also drag it into the message box.": "Envia un fitxer des del teu ordinador: PDF, Word, Excel, imatge. També pots arrossegar-lo al camp de missatge.", "Envoyez une seule fois : chaque modèle répond.\u001fSend once: each model answers.": "Envia una vegada: cada model respon.", "Espace administrateur\u001fAdministrator area": "Àrea d'administrador", "Espaces de discussion partagés. Le + crée un canal si vous en avez le droit.\u001fShared discussion spaces. The + creates a channel if you are allowed.": "Espais de discussió compartides. El + crea un canal si tens permís.", "Espaces partagés où l’IA répond quand on la mentionne.\u001fShared spaces where AI answers when mentioned.": "Espais compartits on l'IA respon quan es menciona.", "Essayer dans le chat\u001fTry it in the chat": "Prova-hi en el xat", "Ex. : Tu es un tuteur de statistiques. Réponds avec des exemples simples.\u001fe.g. You are a statistics tutor. Answer with simple examples.": "p. ex. Tu ets el meu tutor de estadístiques. Respon amb exemples concrets.", "Exemple\u001fExample": "Exemple", "Exemple ajouté dans la zone de saisie.\u001fExample added to the message box.": "Exemple afegit al camp de missatge.", "Exemple de System Prompt\u001fExample System Prompt": "Prompt del sistema d'exemple", "Exemple d’instructions\u001fExample instructions": "Instruccions d'exemple", "Exemples : « Projet Alpha » avec ses documents, « Rapport annuel » avec le guide de rédaction, « Candidatures » avec votre CV.\u001fExamples: “Project Alpha” with its documents, “Annual report” with the writing guide, “Applications” with your CV.": "Exemples: 'Projecte Alfa' amb els seus documents, 'Informe anual' amb la guia d'escriptura, 'Sol·licituds' amb el teu CV.", "Exporter la note (texte, Markdown ou PDF).\u001fExport the note (text, Markdown or PDF).": "Exporta la nota (text, Markdown o PDF).", "Extrais les actions de cette note avec responsable et échéance.\u001fExtract action items from this note with owner and deadline.": "Extraiu les tasques d'acció d'aquesta nota amb responsable i termini.", "Exécute du code pour calculer ou analyser des données.\u001fRuns code to calculate or analyze data.": "Executa codi per calcular o analitzar dades.", "Exécute du code pour calculer, analyser un fichier de données ou produire un graphique.\u001fRuns code to calculate, analyze a data file or build a chart.": "Executa codi per calcular, analitzar un fitxer de dades o construir un gràfic.", "Exécute une demande automatiquement (une fois, chaque heure, jour, semaine, mois). Chaque exécution crée une conversation.\u001fRuns a request automatically (once, hourly, daily, weekly, monthly). Each run creates a chat.": "Executa una petició automàticament (una vegada, hora, dia, setmana, mes). Cada execució crea un xat.", "Exécution automatique\u001fAutomation run": "Execució d'automatització", "Exécution de code\u001fCode execution": "Execució de codi", "Fermer\u001fClose": "Tanca", "Fermez le guide et posez votre première question. L’onglet Fonctions reste disponible pour revoir chaque fonctionnalité.\u001fClose the guide and ask your first question. The Features tab stays available to revisit every feature.": "Tanca la guia i fes la teva primera pregunta. La pestanya d'opcions roman disponible per revisar cada funció.", "Fichiers\u001fFiles": "Fitxers", "Fils de discussion\u001fThreads": "Fils", "Filtre de statut\u001fStatus filter": "Filtre d'estat", "Filtrer (toutes, les vôtres…), le mode d’ouverture et l’affichage liste ou grille.\u001fFilter (all, yours…), open mode, and list or grid view.": "Filtra (tots, els teus…), mode d'obertura, i vista de llista o graella.", "Folders › + ouvre cette fenêtre.\u001fFolders › + opens this window.": "Els dossiers obren aquesta finestra.", "Folders › +, donnez un nom.\u001fFolders › +, give a name.": "Dossiers › +, posa un nom.", "Fonction ajoutée par votre organisation. L’interrupteur l’active pour cette conversation.\u001fA feature added by your organization. The switch turns it on for this chat.": "Una funcionalitat afegida per la teva organització. L'interruptor l'activa per a aquesta xat.", "Fonctions\u001fFeatures": "Funcionalitats", "Fonctions intégrées\u001fBuilt-in features": "Funcionalitats incloses", "Gardez les décisions et liens importants en haut.\u001fKeep key decisions and links at the top.": "Manté les decisions clau i els enllaços al capdamunt.", "Guide des outils\u001fTools handbook": "Manual de eines", "Guide d’utilisation de \u001fHow to use ": "Com utilitzar ", "Guide fermé\u001fGuide closed": "Guia tancada", "Guide mis à jour. \u001fGuide updated. ": "Guia actualitzada. ", "Génère une image à partir de la conversation.\u001fGenerates an image from the conversation.": "Genera una imatge a partir de la conversa.", "Génération d’images\u001fImage generation": "Generació d'imatges", "Icône d’information\u001fInfo icon": "Icona d'informació", "Idées de projet\u001fProject ideas": "Idees de projecte", "Idées et inspiration\u001fIdeas & inspiration": "Idees i inspiració", "Il donne accès à vos espaces personnels et à vos réglages. Son contenu dépend de votre rôle.\u001fIt opens your personal spaces and settings. What you see depends on your role.": "Obre els teus espais personals i configuracions. El què veïs depèn del teu paper.", "Illustration simple et moderne d’une équipe en réunion, format 16:9, sans texte.\u001fSimple modern illustration of a team in a meeting, 16:9, no text.": "Il·lustració moderna senzilla d'un equip en una reunió, 16:9, sense text.", "Images\u001fImages": "Imatges", "Indiquez ce qui était faux et, si possible, la bonne information.\u001fSay what was wrong and, if you can, the correct information.": "Diu què va malbé i, si pot, la informació correcta.", "Informations\u001fInfo": "Informació", "Informations à jour avec sources\u001fUp-to-date info with sources": "Informació actualitzada amb fonts", "Insère une demande préparée à l’avance.\u001fInserts a request prepared in advance.": "Insereix una petició preparada amb antelació.", "Interroger des documents\u001fAsk document collections": "Demanda col·leccions de documents", "Interrupteur\u001fSwitch": "Canvia", "Interrupteur d’outil\u001fTool switch": "Interruptor d'eina", "Intégrations\u001fIntegrations": "Integracions", "Intégrations › Tools › activez l’outil utile.\u001fIntegrations › Tools › turn on the tool you need.": "Integracions › Eines › engega l'eina que necessites.", "Intégrations › Tools › ouvre la liste. Activez l’outil, puis demandez simplement ce que vous voulez : l’assistant l’utilisera au bon moment.\u001fIntegrations › Tools › opens the list. Turn the tool on, then just ask: the assistant uses it when needed.": "Integracions › Eines › obre la llista. Engega l'eina, després simplement demana: l'assistent la usarà quan sigui necessari.", "Intégrations › Web Search. Posez ensuite votre question : la réponse affiche les sources consultées.\u001fIntegrations › Web Search. Then ask your question: the answer shows the sources it used.": "Integracions › Cerca web. Després demana la teva pregunta: la resposta mostra les fonts que ha utilitzat.", "Intégrations › activez Code Interpreter.\u001fIntegrations › turn on Code Interpreter.": "Integracions › engega Interpretador de codi.", "Intégrations › activez Web Search.\u001fIntegrations › turn on Web Search.": "Integracions › engega Cerca web.", "Joignez un fichier CSV ou Excel si besoin.\u001fAttach a CSV or Excel file if needed.": "Adjunta un fitxer CSV o Excel si cal.", "Joindre des fichiers à la note comme contexte pour l’assistant.\u001fAttach files to the note as context for the assistant.": "Adjanta fitxers a la nota com a context per l'assistent.", "J’ai déposé le plan du rapport, vos avis ?\u001fI shared the report outline, thoughts?": "Vaig compartir l'esbosc de l'informe, penses?", "La barre latérale\u001fThe sidebar": "La barra lateral", "La demande envoyée à chaque exécution. Soyez complet : elle doit fonctionner sans vous.\u001fThe request sent on each run. Be complete: it must work without you.": "La petició enviada en cada execució. Sigues complet: ha de funcionar sense tu.", "La flèche liste les bases de documents auxquelles vous avez accès. L’assistant cherche la réponse dedans.\u001fThe arrow lists document collections you can access. The assistant searches them for the answer.": "La fletxa llista col·leccions de documents que pots accedir. L'assistent les busca per la resposta.", "La flèche liste vos Notes. Le contenu complet de la note est ajouté au message.\u001fThe arrow lists your Notes. The full note content is added to the message.": "La fletxa llista les teves Notes. El contingut complet de la nota s'afegeix al missatge.", "La flèche liste vos anciennes conversations pour vous appuyer sur un échange précédent.\u001fThe arrow lists your past chats so you can build on an earlier exchange.": "La fletxa llista els teus xats anteriors perquè puguis construir sobre un canvi anterior.", "La flèche ouvre la liste des fichiers que vous avez déjà envoyés, pour les réutiliser sans les téléverser à nouveau.\u001fThe arrow opens files you already uploaded, so you can reuse them without uploading again.": "La fletxa obre fitxers que ja has pujat, així pots reutilitzar-los sense tornar a pujar.", "La flèche à gauche revient au menu Intégrations.\u001fThe left arrow returns to the Integrations menu.": "La fletxa d'esquerra torna al menú Integracions.", "La flèche › ouvre la liste de vos outils. Activez uniquement ceux utiles à la conversation en cours.\u001fThe arrow › opens your tools. Turn on only the ones this conversation needs.": "La fletxa › obre les teves eines. Engega nomé el què necessita aquesta conversa.", "La page Notes\u001fThe Notes page": "La pàgina de Notes", "Lance immédiatement pour tester avant d’attendre l’horaire.\u001fRuns immediately to test before the schedule.": "S'executa immediatament per provar abans del programació.", "Langue, thème, compte\u001fLanguage, theme, account": "Idioma, tema, compte", "Langue, thème, notifications, voix, personnalisation et mémoire.\u001fLanguage, theme, notifications, voice, personalization and memory.": "Idioma, tema, notificacions, veu, personalització i memòria.", "Le Calendrier\u001fThe Calendar": "El calendari", "Le bouton + : ajouter du contexte\u001fThe + button: add context": "El botó : afegeix context", "Le bouton + et ses menus\u001fThe + button and its menus": "El botó i els seus menús", "Le bouton Intégrations\u001fThe Integrations button": "El botó d'Integracions", "Le bouton à côté de + regroupe les outils et fonctions. Chaque interrupteur s’applique à la conversation en cours.\u001fThe button next to + groups tools and features. Each switch applies to the current conversation.": "El botó al costat de + agrupa eines i funcionalitats. Cada interruptor s'aplica a la conversa actual.", "Le calendrier ne se synchronise pas avec Outlook ou Google Agenda.\u001fThe calendar does not sync with Outlook or Google Calendar.": "El calendari no sincronitza amb Outlook o Google Calendar.", "Le guide a été enrichi. Parcourez le sommaire pour voir les nouveautés.\u001fThe guide has been updated. Browse the contents to see what is new.": "La guia s'ha actualitzada. Navega pels continguts per veure què hi ha de nou.", "Le menu utilisateur\u001fThe user menu": "El menú d'usuari", "Le menu ⋯ d’une note\u001fThe note ⋯ menu": "El menú de la nota ⋯", "Le nom en haut indique l’assistant qui va répondre.\u001fThe name at the top shows which assistant will answer.": "El nom a dalt mostra què assistent respon.", "Les actions sous chaque réponse\u001fActions under each answer": "Accions sota cada resposta", "Les automatisations\u001fAutomations": "Automatitzacions", "Les canaux : travailler à plusieurs avec l’IA\u001fChannels: working together with AI": "Canals: treballant junts amb IA", "Les canaux dont vous êtes membre. Un point signale les messages non lus.\u001fChannels you belong to. A dot marks unread messages.": "Els canals als quals pertanys. Un punet marca missatges sense llegir.", "Les commandes de la zone de saisie\u001fThe message box controls": "Els controls del camp de missatge", "Les dernières icônes sont des actions propres à votre compte. Survolez-les pour voir leur nom.\u001fThe last icons are actions specific to your account. Hover them to see their name.": "Les últimes icones són accions específiques del teu compte. Passa el cursor per veure'n el nom.", "Les outils intégrés de l’assistant\u001fThe assistant’s built-in tools": "Les eines incloses de l'assistent", "Les questions suggérées\u001fSuggested questions": "Preguntes suggerides", "Les suggestions changent selon l’assistant choisi : elles montrent ce qu’il sait bien faire.\u001fSuggestions change with the selected assistant: they show what it does best.": "Les suggerències canvien segons l'assistent seleccionat: mostren el què fa millor.", "Lire une page web\u001fRead a web page": "Llegir una pàgina web", "Lire à voix haute\u001fRead aloud": "Llegir en veu alta", "Liste de tâches\u001fTask list": "Llista de tasques", "Liste des canaux\u001fChannel list": "Llista de canals", "Liées à l’assistant\u001fTied to the assistant": "Enllaçat a l'assistent", "L’assistant agit pour vous\u001fThe assistant acts for you": "L'assistent actua per a tu", "L’assistant cherche la réponse dans une collection de documents et cite les passages.\u001fThe assistant searches a document collection and cites passages.": "L'assistent busca una col·lecció de documents i cita passatges.", "L’assistant lance les outils dont il a besoin sans vous interrompre. Idéal pour les recherches et lectures.\u001fThe assistant runs the tools it needs without interrupting you. Best for searches and reading.": "L'assistent executa les eines que necessita sense interrompre't. El més bona per a cerques i lectura.", "L’assistant peut se tromper avec assurance. Vous restez responsable de ce que vous en faites.\u001fThe assistant can be confidently wrong. You stay responsible for what you do with it.": "L'assistent pot estar convençudament equivocat. Tu ets responsable del què fas amb ell.", "L’assistant retient des informations utiles d’une conversation à l’autre.\u001fThe assistant keeps useful details across chats.": "L'assistent manté detalls útils entre xats.", "L’assistant utilisé. Ses outils et réglages s’appliquent.\u001fThe assistant used. Its tools and settings apply.": "L'assistent ha utilitzat. Les seves eines i configuració s'apliquen.", "L’assistant écrit et exécute du code pour obtenir un résultat exact.\u001fThe assistant writes and runs code to get an exact result.": "L'assistent escriu i executa codi per obtenir un resultat exacte.", "L’historique. La flèche replie la liste ; le menu ⋯ propose d’archiver ou supprimer. Clic droit sur une conversation pour l’épingler, la renommer ou la déplacer.\u001fYour history. The arrow collapses the list; ⋯ offers archive or delete. Right-click a chat to pin, rename or move it.": "La teva història. La fletxa trenca la llista; ⋯ ofereix arxivar o suprimir. Clica amb botó dret a un xat per fixar-lo, reanomenar-lo o moure'l.", "L’icône réglages permet d’ajuster ses options personnelles.\u001fThe settings icon adjusts its personal options.": "L'icona de configuració ajusta les teves opcions personals.", "L’écran d’accueil et les suggestions\u001fHome screen and suggestions": "Pantalla d'inici i suggerències", "L’éditeur de note\u001fThe note editor": "L'editor de notes", "Masquer la barre\u001fHide sidebar": "Amaga barra lateral", "Mauvaise réponse\u001fBad response": "Resposta malbé", "Menu utilisateur\u001fUser menu": "Menú d'usuari", "Menu utilisateur › Automations. Une automatisation envoie une demande à heure fixe : chaque exécution crée une conversation avec la réponse.\u001fUser menu › Automations. An automation sends a request on a schedule: each run creates a chat with the answer.": "Menú d'usuari › Automatitzacions. Una automatització envia una petició segons un horari: cada execució crea un xat amb la resposta.", "Menu utilisateur › Calendar.\u001fUser menu › Calendar.": "Menú d'usuari › Calendari.", "Menu utilisateur › Calendar. Un agenda personnel avec rappels, que l’assistant peut aussi gérer pour vous.\u001fUser menu › Calendar. A personal calendar with reminders that the assistant can also manage for you.": "Menú d'usuari › Calendari. Un calendari personal amb recordatoris que l'assistent també pot gestionar per a tu.", "Messages épinglés\u001fPinned messages": "Missatges fixats", "Met en pause ou relance sans supprimer.\u001fPauses or resumes without deleting.": "Pausa o retoma sense suprimir.", "Micro : parlez, relisez le texte, envoyez.\u001fMic: speak, review the text, send.": "Mic: parla, revisa el text, envia.", "Mini calendrier\u001fMini calendar": "Mini calendari", "Modifier\u001fEdit": "Edita", "Modifiez les permissions sur un groupe de test avant de les appliquer à tous.\u001fChange permissions on a test group before applying them to everyone.": "Canvia els permisos en un grup de prova abans d'aplicar-los a tothom.", "Modèle généraliste\u001fGeneral model": "Model general", "Modèle raisonnement\u001fReasoning model": "Model de raonament", "Modèles de demande\u001fSaved prompts": "Avísos guardats", "Mots de passe, codes, identifiants\u001fPasswords, codes, credentials": "Contrasenyies, codis, credencials", "Mémoire\u001fMemory": "Memòria", "Naviguer dans vos espaces\u001fMoving between spaces": "Mou entre espais", "Ne collez jamais de mot de passe dans la conversation.\u001fNever paste a password into the chat.": "Mai enganxa un contrasenya en el xat.", "Ne joignez que des documents que vous avez le droit d’utiliser.\u001fOnly attach documents you are allowed to use.": "Només adjunta documents que tens permís d'usar.", "New Automation. La flèche propose Import JSON et Export JSON pour copier vos automatisations.\u001fNew Automation. The arrow offers Import JSON and Export JSON to copy your automations.": "Nova automatització. La fletxa ofereix Importa JSON i Exporta JSON per copiar les teves automatitzacions.", "New Event : titre, date, lieu, répétition, rappel.\u001fNew Event: title, date, location, repeat, reminder.": "Esdeveniment nou: títol, data, localització, repetició, recordatori.", "New Event ouvre ce formulaire, prérempli à la date du jour.\u001fNew Event opens this form, prefilled with today’s date.": "L'esdeveniment nou obre aquest formulari, pre-omple amb la data d'avui.", "No Repeat, Daily, Monday – Friday, Weekly, Monthly ou Yearly. Pas plus souvent qu’une fois par jour : pour cela, utilisez une automatisation.\u001fNo Repeat, Daily, Monday – Friday, Weekly, Monthly or Yearly. No more than once a day: use an automation for that.": "Sense Repetir, Diari, Dilluns – Divendres, Setmanal, Mensual o Anual. No més d'una vegada al dia: usa una automatització per això.", "Nom, fréquence et prochaine exécution. Cliquez pour ouvrir l’éditeur.\u001fName, schedule and next run. Click to open the editor.": "Nom, programació i propera execució. Clica per obrir l'editor.", "Nombre de notes accessibles.\u001fNumber of notes you can access.": "Nombre de notes que pots accedir.", "Not factually correct : erreur de fait. Didn’t fully follow instructions : consigne ignorée. Refused when it shouldn’t have : refus injustifié. Too verbose : trop long.\u001fNot factually correct: wrong facts. Didn’t fully follow instructions: ignored a request. Refused when it shouldn’t have: unjustified refusal. Too verbose: too long.": "No és factualment correcte: dades errònies. No ha seguit completament les instruccions: ha ignorat una petició. Ha rebutjat quan hauria d'haver actuat: negació sense justificació. Molt verbos: massa llarg.", "Note de 1 à 5\u001fScore 1 to 5": "Puntuació de 1 a 5", "Note de 6 à 10\u001fScore 6 to 10": "Puntuació de 6 a 10", "Noter les bonnes et mauvaises réponses\u001fRating good and bad answers": "Valorant bones i males respostes", "Notes de réunion\u001fMeeting notes": "Notes de reunió", "Notes › Create.\u001fNotes › Create.": "Notes › Crea.", "Notez les réponses pour aider à améliorer les assistants.\u001fRate answers to help improve assistants.": "Puntuació les respostes per ajudar a millorar assistents.", "Nouvelle conversation dans un dossier\u001fNew chat in a folder": " Nou xat en un dossier", "N’activez que ce dont vous avez besoin : trop d’outils actifs ralentit et disperse les réponses.\u001fOnly turn on what you need: too many active tools slows and scatters answers.": "Només engega el què necessites: moltes eines actives despren i dispersen les respostes.", "N’y enregistrez aucune donnée sensible.\u001fDo not store sensitive data there.": "No hi emmagatzema dades sensibles.", "Obligatoire. Un nom clair.\u001fRequired. A clear name.": "Obligatori. Un nom clar.", "Once, Hourly, Daily, Weekly, Monthly ou Custom (règle avancée).\u001fOnce, Hourly, Daily, Weekly, Monthly or Custom (advanced rule).": "Una vegada, cada hora, diari, setmanal, mensual o Personal (regla avançada).", "Optionnel : salle ou lien de visio.\u001fOptional: room or video link.": "Opcional: enllaç de sala o vídeo.", "Optionnel : une image pour reconnaître le dossier.\u001fOptional: an image to recognize the folder.": "Opcional: una imatge per reconèixer el dossier.", "Ordre du jour, liens, documents à préparer.\u001fAgenda, links, documents to prepare.": "Ordre del dia, enllaços, documents per preparar.", "Ordre du jour…\u001fAgenda…": "Ordena diari…", "Organiser et collaborer\u001fOrganize and collaborate": "Organitzar i col·laborar", "Organiser vos projets\u001fOrganizing your projects": "Organitzant els teus projectes", "Ou demandez à l’assistant : « ajoute le partiel de stats jeudi 14h ».\u001fOr ask the assistant: “add the stats exam on Thursday at 2pm”.": "O demana a l'assistent: 'afegeix l'examen d'estadístiques dijous a les 4pm'.", "Outil 1\u001fTool 1": "Eina 1", "Outil 2\u001fTool 2": "Eina 2", "Outil utilisé\u001fTool used": "Eina utilitzada", "Outils de votre compte\u001fTools on your account": "Eines al teu compte", "Outils intégrés\u001fBuilt-in tools": "Eines incloses", "Outils, recherche web, fonctions\u001fTools, web search, features": "Eines, cerca web, funcionalitats", "Ouvre le formulaire d’avis positif. Voir chapitre Avis.\u001fOpens the positive rating form. See the Feedback chapter.": "Obre el formulari de puntuació positiva. Veure el capítol Retroalimentació.", "Ouvre le formulaire pour signaler un problème.\u001fOpens the form to report a problem.": "Obre el formulari per denunciar un problema.", "Ouvre ou ferme le panneau assistant de la note.\u001fOpens or closes the note’s assistant panel.": "Obre o tanca el panell de l'assistent de la nota.", "Ouvre vos Notes.\u001fOpens your Notes.": "Obre les teves Notes.", "Ouvrez Playground.\u001fOpen Playground.": "Obre Playground.", "Ouvrez Settings › Personalization › Memory.\u001fOpen Settings › Personalization › Memory.": "Obre Configuració › Personalització › Memòria.", "Ouvrez le dossier puis New Chat : la conversation hérite de ses réglages.\u001fOpen the folder then New Chat: the chat inherits its settings.": "Obre el dossier després Nou Xat: el xat hereu la seva configuració.", "Ouvrez le panneau Chat et utilisez une suggestion.\u001fOpen the Chat panel and use a suggestion.": "Obre el panell de xats i usa una suggerència.", "Ouvrez les sources pour vérifier.\u001fOpen the sources to check.": "Obre les fonts per comprovar.", "Ouvrez un canal.\u001fOpen a channel.": "Obre un canal.", "Ouvrir le chat\u001fOpen chat": "Obre xat", "Ouvrir les sources affichées\u001fOpen the cited sources": "Obre les fonts citades", "Où la trouver\u001fWhere to find it": "On trobar-la", "PDF, Word, Excel, images\u001fPDF, Word, Excel, images": "PDF, Word, Excel, imatges", "Paramètres\u001fSettings": "Configuració", "Parcourt et lit les bases de documents accessibles.\u001fBrowses and reads accessible document collections.": "Navega i llegeix col·leccions de documents accessibles.", "Parlez : votre voix est transcrite en texte, que vous relisez avant d’envoyer.\u001fSpeak: your voice becomes text you can review before sending.": "Parla: la teva veu esdevé text que pots revisar abans d'enviar.", "Permissions des outils\u001fTool permissions": "Permisos d'eines", "Plan de projet\u001fProject plan": "Plan de projecte", "Plan du projet\u001fProject outline": "Esbós de projecte", "Plus le contexte est précis, meilleure est la réponse. N’ajoutez que ce qui est utile à la question.\u001fPrecise context gives better answers. Only add what the question needs.": "Un context precís dona millors respostes. Només afegeix el què la pregunta necessita.", "Plusieurs réponses côte à côte\u001fSeveral answers side by side": "Vanes respostes al costat", "Plusieurs réponses identiques peuvent partager la même erreur.\u001fMatching answers can share the same mistake.": "Respostes coincidents poden compartir el mateix error.", "Point équipe 9:00\u001fTeam standup 9:00": "Estàndup d'equip 9:00", "Posez la même question à plusieurs modèles et comparez.\u001fAsk several models the same question and compare.": "Demana la mateixa pregunta a diversos models i compara.", "Posez une question précise.\u001fAsk a precise question.": "Demana una pregunta precisa.", "Posez votre propre demande sur la note.\u001fAsk your own request about the note.": "Demanda la teva pròpia petició sobre la nota.", "Posez votre question sur la page.\u001fAsk about the page.": "Preguntes sobre la pàgina.", "Pour les actions qui modifient quelque chose, réglez + › Tool Permissions sur Ask for approval.\u001fFor actions that change something, set + › Tool Permissions to Ask for approval.": "Per accions que canviïn alguna cosa, establi + › Permisos d'eina a Demana aprovació.", "Pour une demande en plusieurs étapes, l’assistant tient une liste de tâches et la coche au fur et à mesure.\u001fFor multi-step requests, the assistant keeps a task list and ticks items off.": "Per a petició multi-pas, l'assistent manté una llista de tasques i coxa les caselles.", "Pour utiliser une note dans une conversation : + › Attach Notes.\u001fTo use a note in a chat: + › Attach Notes.": "Per usar una nota en un xat: + › Adjunta Notes.", "Pourquoi utiliser des dossiers\u001fWhy use folders": "Per què utilitzar dossiers", "Prendre une capture d’écran d’une fenêtre ou de l’écran et la joindre au message.\u001fTake a screenshot of a window or your screen and attach it.": "Fes una captura de pantalla d'una finestra o de la teva pantalla i adjunta-la.", "Produit une nouvelle version. Vous pouvez naviguer entre les versions.\u001fProduces a new version. You can switch between versions.": "Produeix una nova versió. Pots alternar entre versions.", "Programme un résumé des actualités du secteur chaque lundi à 8h.\u001fSchedule an industry news digest every Monday at 8am.": "Programa un resum setmanal de notícies industrials cada dilluns a les 8am.", "Projets avec consignes et documents\u001fProjects with instructions and documents": "Projectes amb instruccions i documents", "Préciser votre demande si besoin\u001fRefine your request when needed": "Refina la teva petició si cal", "Précisez la période, le pays et le type de source souhaité.\u001fSpecify the period, country and type of source you want.": "Especifici el període, país i tipus de font que voleu.", "Précisez période et sources souhaitées.\u001fSpecify the period and sources you want.": "Especifici el període i les fonts que vols.", "Précédent\u001fBack": "Arrere", "Préférences retenues\u001fRemembered preferences": "Preferències recordades", "Présentation du guide\u001fAbout this guide": "Sobre aquesta guia", "Quand la zone est vide, ce bouton lance le mode vocal. Dès que vous écrivez, il devient le bouton d’envoi.\u001fWhen the box is empty, this button starts voice mode. Once you type, it becomes the send button.": "Quan la caixa està buida, aquest botó engega el mode veu. Un cop comencis a escriure, esdevé el botó d'enviar.", "Quelles sont les nouveautés officielles sur ce sujet en 2026 ? Donne les liens.\u001fWhat was officially published on this topic in 2026? Give the links.": "Què s'ha publicat oficialment sobre aquest tema l'any 2026? Dona els enllaços.", "Quelles sont les règles en vigueur en 2026 ? Cite la source officielle.\u001fWhat rules apply in 2026? Cite the official source.": "Quines regles són vàlides a l'any 2026? Cita la font oficial.", "Qui peut voir ou modifier la note. Par défaut elle est privée.\u001fWho can view or edit the note. Private by default.": "Qui pot veure o editar la nota. Privat per defecte.", "Qu’est-ce que j’ai dans mon calendrier cette semaine ?\u001fWhat is on my calendar this week?": "Què hi ha al meu calendari aquesta setmana?", "Range automatiquement les conversations produites dans un de vos dossiers.\u001fFiles the chats it creates into one of your folders.": "Emmagatzema els xats que crea dins d'un dels teus dossiers.", "Rangez les conversations d’un projet et partagez consignes et documents entre elles.\u001fGroup a project’s chats and share instructions and documents between them.": "Agrupar els xats d'un projecte i compartir instruccions i documents entre ells.", "Rappel quotidien\u001fDaily reminder": "Recordatori diari", "Rappelle-moi vendredi à 15h de relancer le prestataire.\u001fRemind me on Friday at 3pm to follow up with the supplier.": "Recorda'm divendres a les 3pm per fer seguiment amb el proveïdor.", "Recherche dans les titres et le contenu.\u001fSearches titles and content.": "Busca títols i continguts.", "Recherche les actualités du secteur de la semaine et résume-les en 5 points avec les liens.\u001fSearch this week’s industry news and summarize it in 5 points with links.": "Cerca les notícies industrials d'aquesta setmana i resumeix en 5 punts amb enllaços.", "Recherche sur Internet et cite les pages consultées.\u001fSearches the internet and cites the pages used.": "Cerca a Internet i cita les pàgines utilitzades.", "Recherche web\u001fWeb search": "Cerca web", "Recherche, lit, crée et met à jour vos notes.\u001fSearches, reads, creates and updates your notes.": "Cerca, llegeix, crea i actualitza les teves notes.", "Rechercher\u001fSearch": "Cerca", "Rechercher des conversations\u001fSearch chats": "Cerca xats", "Rechercher des fichiers\u001fSearch files": "Cerca fitxers", "Rechercher des notes\u001fSearch notes": "Cerca notes", "Regroupez vos conversations par projet. Le + crée un dossier.\u001fGroup chats by project. The + creates a folder.": "Agrupar xats per projecte. El + crea un dossier.", "Relisez les résultats : l’automatisation s’exécute sans vous.\u001fReview results: the automation runs without you.": "Revisa resultats: l'automatització s'executa sense tu.", "Remplissez les champs puis Create. L’éditeur permet ensuite de lancer, suspendre et suivre les exécutions.\u001fFill in the fields then Create. The editor then lets you run, pause and monitor executions.": "Omple els camps, després Crea. L'editor llavors et permet executar, pausar i controlar execucions.", "Replie la barre pour gagner de la place. Cliquez à nouveau pour la rouvrir.\u001fCollapses the sidebar for more room. Click again to reopen.": "Retrau la barra lateral per tenir més espai. Clica novament per obrir-la.", "Reprenez quand vous voulez\u001fResume anytime": "Retoma quan vulguis", "Repérez la ligne « outil utilisé » et ouvrez-la pour vérifier.\u001fLook for the “tool used” line and open it to check.": "Busca la línia 'Eina utilitzada' i obre-la per comprovar.", "Respectez les règles de votre organisation sur l’usage de l’IA.\u001fFollow your organization’s rules on using AI.": "Segues les regles de la teva organització sobre ús d'IA.", "Retient ou retrouve une préférence : « souviens-toi que je travaille sur le projet Alpha ».\u001fSaves or recalls a preference: “remember I work on Project Alpha”.": "Desa o recupera una preferència: 'recorda que he treballat al Projecte Alfa'.", "Retour\u001fBack": "Arrere", "Retrouve dans mes notes ce que j’ai écrit sur le projet Alpha.\u001fFind what I wrote about Project Alpha in my notes.": "Tria què he escrit sobre el Projecte Alfa a les meves notes.", "Retrouve une ancienne conversation : « qu’avions-nous décidé sur le budget ? ».\u001fFinds a past chat: “what did we decide about the budget?”.": "Troba un xat anterior: 'què hem decidit sobre el pressupost?'.", "Retrouve une conversation par mot-clé.\u001fFinds a chat by keyword.": "Troba un xat per paraula clau.", "Retrouver une automatisation par son nom.\u001fFind an automation by name.": "Tria una automatització pel nom.", "Revenez sur une modification, y compris celles faites par l’assistant.\u001fUndo any change, including those made by the assistant.": "Desfà qualsevol canvi, inclosos els fets per l'assistent.", "Revient à aujourd’hui. Les flèches passent à la période précédente ou suivante.\u001fReturns to today. Arrows move to the previous or next period.": "Torna a avui. Fletxes mouen al període anterior o següent.", "Rouvrir le guide\u001fReopen guide": "Reobre la guia", "Règlement intérieur\u001fInternal rules": "Regles internes", "Règles d’utilisation\u001fAcceptable use": "Ús acceptable", "Règles essentielles\u001fEssential rules": "Regles essencials", "Réactions\u001fReactions": "Reaccions", "Rédiger avec l’assistant\u001fWrite with the assistant": "Escriu amb l'assistent", "Rédiger avec l’assistant\u001fWriting with the assistant": "Escriptura amb l'assistent", "Rédiger un e-mail\u001fDraft an email": "Brossa un correu electrònic", "Rédigez à gauche, travaillez avec l’assistant à droite.\u001fWrite on the left, work with the assistant on the right.": "Escriu a l'esquerra, treballa amb l'assistent a la dreta.", "Réglez + › Tool Permissions sur Ask for approval pour valider chaque action.\u001fSet + › Tool Permissions to Ask for approval to confirm each action.": "Estableix + › Permisos d'eina a Demana aprovació per confirmar cada acció.", "Réglez l’interface selon vos préférences.\u001fAdjust the interface to your preferences.": "Adapta la interfície als teus preferències.", "Régénérer\u001fRegenerate": "Regenera", "Répondez en fil pour séparer les sujets.\u001fReply in threads to separate topics.": "Respon en fils per separar temes.", "Répondez à un message précis pour garder les sujets séparés.\u001fReply to a specific message to keep topics separate.": "Respon a un missatge concret per mantenir els temes separats.", "Réponds uniquement à partir de ces documents et indique la section utilisée.\u001fAnswer only from these documents and name the section used.": "Respon nomé a partir d'aquests documents i indica la secció usada.", "Réponses\u001fAnswers": "Respostes", "Réservé aux administrateurs\u001fAdmins only": "Només administradors", "Réservé aux administrateurs : tester un modèle avec ses paramètres bruts, hors conversation.\u001fAdmins only: test a model with raw parameters, outside a chat.": "Només administradors: prova un model amb paràmetres raw, fora d'un xat.", "Réservé aux administrateurs : utilisateurs, groupes et permissions, réglages, évaluations.\u001fAdmins only: users, groups and permissions, settings, evaluations.": "Només administradors: configuració d'usuaris, grups i permisos, avaluacions.", "Résume cette page en 5 points pour quelqu’un qui découvre le sujet.\u001fSummarize this page in 5 points for someone new to the topic.": "Resumeix aquesta pàgina en 5 puns per a algú nou en el tema.", "Résumer un document\u001fSummarize a document": "Resumeix un document", "Résumé hebdo 8:00\u001fWeekly digest 8:00": "Resum setmanal 8:00", "Résumé hebdomadaire des actualités\u001fWeekly news digest": "Resum setmanal de notícies", "Réunion de projet\u001fProject meeting": "Reunió projecte", "Réunion projet 10:00\u001fProject meeting 10:00": "Reunió projecte 10:00", "Réutiliser une conversation\u001fReuse a conversation": "Reutilitza una conversa", "Salle 204\u001fRoom 204": "Sala 204", "Sans recherche web, l’assistant répond avec des connaissances qui peuvent être anciennes.\u001fWithout web search, the assistant answers from knowledge that may be outdated.": "Sense cerca web, l'assistent respon des del coneixement que pot estar desactualitzat.", "Select Knowledge ajoute une base existante, Upload envoie vos fichiers. Ils servent de contexte à chaque conversation du dossier.\u001fSelect Knowledge adds an existing collection, Upload sends your files. They become context for every chat in the folder.": "Tria Coneixement afegui una col·lecció existent, Carrega envia els teus fitxers. Esdevenen context per a cada xat del dossier.", "Selon sa configuration, l’assistant peut agir directement : consulter la date, chercher dans vos notes, créer un événement… Vous le demandez en langage naturel, il choisit l’outil.\u001fDepending on its setup, the assistant can act directly: check the date, search your notes, create an event… You ask in plain language and it picks the tool.": "Segons la configuració, l'assistent pot actuar directament: comprova la data, cerca les teves notes, crea un esdeveniment… Demanes en llenguatge senzill i ell tria l'eina.", "Settings › General pour la langue et le thème.\u001fSettings › General for language and theme.": "Configuració › General per a idioma i tema.", "Si la réponse s’arrête en cours de route, l’assistant reprend là où il s’était arrêté.\u001fIf the answer stops midway, the assistant picks up where it left off.": "Si la resposta s'atura a mig camí, l'assistent continua on ho va deixar.", "Signaler une mauvaise réponse\u001fReporting a bad answer": "Denunciant una mala resposta", "Sommaire\u001fContents": "Continguts", "Sous la zone de saisie d’une nouvelle conversation, des suggestions montrent des usages typiques de l’assistant.\u001fBelow the message box of a new chat, suggestions show typical uses of the assistant.": "Sota el camp de missatge d'un xat nou, les suggerències mostren ús típics de l'assistent.", "Sous une réponse\u001fUnder an answer": "Sota una resposta", "Sous une réponse : haut-parleur pour l’écouter.\u001fUnder an answer: speaker to listen.": "Sota una resposta: altaveu per escoltar.", "Statut visible par les autres\u001fStatus shown to others": "Estat mostrat als altres", "Suivant\u001fNext": "Següent", "Support\u001fSupport": "Suport", "Supprime définitivement la note.\u001fPermanently deletes the note.": "Suprimeix permanentment la nota.", "Supprime l’automatisation et son historique.\u001fDeletes the automation and its history.": "Suprimeix l'automatització i la seva història.", "Survolez une réponse : cette barre apparaît. Chaque icône a un rôle précis.\u001fHover an answer: this bar appears. Each icon has a specific role.": "Passa el cursor sobre una resposta: apareix aquesta barra. Cada icona té un rol específic.", "Survolez une réponse.\u001fHover an answer.": "Passa el cursor sobre una resposta.", "Survolez-la pour lire le rôle de l’assistant avant de le choisir.\u001fHover it to read what the assistant is for before choosing it.": "Passa el cursor per llegir per què serveix l'assistent abans de triar-lo.", "Suspend sans perdre les réglages.\u001fSuspends without losing settings.": "Pausa sense perdre configuració.", "Sélecteur d’assistant\u001fAssistant selector": "Selector d'assistent", "Tapez /\u001fType /": "Escriu /", "Tapez / au début du message.\u001fType / at the start of the message.": "Escriu / a l'inici del missatge.", "Tapez @ et choisissez un assistant.\u001fType @ and pick an assistant.": "Escriu @ i tria un assistent.", "Tapez @ puis choisissez un assistant : il répond dans un fil sous votre message, sans encombrer le canal.\u001fType @ then pick an assistant: it replies in a thread under your message, keeping the channel tidy.": "Escriu @ i tria'n un assistent: respon en un fil sota el teu missatge, mantenint net el canal.", "Tapez un nom pour filtrer. « All » filtre par catégorie.\u001fType a name to filter. “All” filters by category.": "Escriu un nom per filtrar. 'Tot' filtra per categoria.", "Tester un modèle avec ses paramètres, hors conversation.\u001fTest a model with its parameters, outside a chat.": "Prova un model amb els seus paràmetres, fora d'un xat.", "Tester un modèle en direct avec prompt système et paramètres, sans créer de conversation.\u001fTest a model live with system prompt and parameters, without creating a chat.": "Prova un model en directe amb prompt del sistema i paràmetres, sense crear un xat.", "Testez avec Run now, suivez les Execution logs.\u001fTest with Run now, check Execution logs.": "Prova amb Execució ara, comprova els registres d'execució.", "Testez chaque changement sur un compte non administrateur.\u001fTest every change with a non-admin account.": "Prova cada canvi amb un compte que no sigui administrador.", "Testez toujours avec Run now. Vous pouvez aussi demander dans une conversation : « programme un résumé tous les jours à 9h ».\u001fAlways test with Run now. You can also ask in a chat: “schedule a summary every day at 9am”.": "Prova sempre amb Execució ara. També pots demanar en un xat: 'programa un resum cada dia a les 9am'.", "Title, Instructions, Model, Schedule, Folder.\u001fTitle, Instructions, Model, Schedule, Folder.": "Títol, Instruccions, Model, Programa, Dossier.", "Titre\u001fTitle": "Títol", "Tous les membres du canal lisent ce que vous publiez. Vérifiez qui y a accès avant de partager un document.\u001fEvery channel member reads what you post. Check who has access before sharing a document.": "Tots els membres del canal llegiran el què publiquis. Comprova qui té accés abans de compartir un document.", "Tous les membres voient vos messages.\u001fAll members see your messages.": "Tots els membres veuen els teus missatges.", "Tout part de cette zone. Voici à quoi sert chaque bouton.\u001fEverything starts here. Here is what each button does.": "Tot comença aquí. Aquí és el què fa cada botó.", "Toutes les actions sur la note.\u001fAll actions for the note.": "Totes les accions per la nota.", "Toutes les fonctions disponibles pour vous\u001fEvery feature available to you": "Tota la funcionalitat disponible per a tu", "Toutes, actives ou en pause.\u001fAll, active or paused.": "Tots, actius o en pausa.", "Transforme cette note en compte rendu : décisions, responsables, échéances.\u001fTurn this note into minutes: decisions, owners, deadlines.": "Converteix aquesta nota en una acta: decisions, responsables i terminis.", "Travailler à plusieurs\u001fWorking together": "Treballant junts", "Trouve les informations officielles les plus récentes sur ce sujet et donne le lien de chaque source.\u001fFind the latest official information on this topic and give the link to each source.": "Troba la informació oficial més actualitzada sobre aquest tema i dona l'enllaç de cada font.", "Tu es mon tuteur en statistiques. Explique avec des exemples concrets, vérifie mes calculs et pose-moi une question pour valider ma compréhension.\u001fYou are my statistics tutor. Explain with concrete examples, check my calculations and ask me a question to confirm I understood.": "Tu ets el meu tutor d'estadístiques. Expliqua amb exemples concrets, comprova els teus càlculs i demana'm una pregunta per confirmar que he entès.", "Tutoriels\u001fTutorials": "Tutorials", "Télécharger, partager, épingler, supprimer. Voir étape suivante.\u001fDownload, share, pin, delete. See next step.": "Descarrega, comparteix, fixa o suprimeix. Veure pas següent.", "Un assistant spécialisé répond à partir de ses documents. Pour résumer vos fichiers ou chercher sur Internet, prenez un modèle généraliste.\u001fA specialized assistant answers from its own documents. To summarize your files or search the web, pick a general model.": "Un assistent especialitzat respon a partir dels seus propis documents. Per resumir els teus fitxers o cercar a Internet, tria un model general.", "Un canal est un espace partagé en temps réel. L’IA n’intervient que si vous la mentionnez avec @.\u001fA channel is a real-time shared space. AI only joins in when you mention it with @.": "Un canal és un espai compartit en temps real. La IA només s'uneix quan li poses @.", "Un dossier est un projet : il range vos conversations ET donne à l’assistant les mêmes consignes et documents pour chaque conversation qu’il contient.\u001fA folder is a project: it groups your chats AND gives the assistant the same instructions and documents for every chat inside.": "Un dossier és un projecte: agrupa les teves xats i dona les mateixes instruccions i documents a cada xat d'interior.", "Un emoji et un court message pour indiquer votre disponibilité.\u001fAn emoji and a short message to show your availability.": "Un emoji i un missatge curt per mostrar la teva disponibilitat.", "Un emoji pour approuver sans ajouter de message.\u001fAn emoji to agree without adding a message.": "Un emoji per estar d'acord sense afegir un missatge.", "Un nom clair, sans donnée sensible.\u001fA clear name, no sensitive data.": "Un nom clar, sense dades sensibles.", "Un nom qui dit ce que produit l’automatisation.\u001fA name saying what it produces.": "Un nom que diu el què produeix.", "Un événement\u001fAn event": "Un esdeveniment", "Une automatisation\u001fAn automation": "Una automatització", "Une note\u001fA note": "Una nota", "Une phrase suffit pour dire ce qui vous a aidé.\u001fOne sentence is enough to say what helped.": "Una frase és suficient per dir què ha ajudat.", "Update your status, choisissez un emoji et un texte.\u001fUpdate your status, pick an emoji and text.": "Actualitza el teu estat, tria un emoji i text.", "Utilisateurs, groupes et permissions (qui voit Notes, Calendar, Automations…), réglages généraux, évaluations des modèles à partir des avis.\u001fUsers, groups and permissions (who sees Notes, Calendar, Automations…), general settings, model evaluations from feedback.": "Usuaris, grups i permisos (què veu Notes, Calendari, Automatitzacions…), configuració general, avaluació de models des retroalimentació.", "Utilisateurs, groupes, permissions, réglages et évaluations.\u001fUsers, groups, permissions, settings and evaluations.": "Usuaris, grups, permisos, configuració i avalucacions.", "Utiliser cet assistant par défaut pour vos nouvelles conversations.\u001fUse this assistant by default for new chats.": "Usa aquest assistent per defecte per a nous xats.", "Utiliser l’IA de façon responsable\u001fUsing AI responsibly": "Utilitzant IA de manera responsable", "Utiliser un outil\u001fUsing a tool": "Utilitzant una eina", "Utilisez Ask for approval pour valider les actions.\u001fUse Ask for approval to confirm actions.": "Usa Demana aprovació per confirmar accions.", "V\u001fY": "Y", "Veille\u001fWatch": "Veure", "Veille concurrentielle\u001fMarket watch": "Mercat a la vista", "Visite guidée\u001fGuided tour": "Visita guiada", "Voir toutes les fonctions\u001fSee all features": "Veure totes les funcionalitats", "Voix\u001fVoice": "Veu", "Vos assistants épinglés, pour les ouvrir en un clic.\u001fYour pinned assistants, one click away.": "Els teus assistents fixats, a un clic de distància.", "Vos automatisations sont privées : personne d’autre ne peut les voir ni les lancer.\u001fYour automations are private: nobody else can see or run them.": "Les teves automatitzacions són privades: ningú més no pot veure-les o executar-les.", "Vos documents personnels, avec un assistant intégré. Voir chapitre Notes.\u001fYour personal documents with a built-in assistant. See the Notes chapter.": "Els teus documents personals amb un assistent integrat. Veure el capítol Notes.", "Votre agenda personnel, avec rappels. Voir chapitre Calendrier.\u001fYour personal calendar with reminders. See the Calendar chapter.": "El teu calendari personal amb recordatoris. Veure el capítol Calendari.", "Votre compte\u001fYour account": "El teu compte", "Votre nom\u001fYour name": "El teu nom", "Votre nom (en bas à gauche)\u001fYour name (bottom left)": "El teu nom (inferior esquerra)", "Votre nom en bas à gauche\u001fYour name, bottom left": "El teu nom, inferior esquerra", "Votre question\u001fYour question": "La teva pregunta", "Vous\u001fYou": "Tu", "Vous voyez ce chapitre car votre compte est administrateur.\u001fYou see this chapter because your account is an administrator.": "Veus aquest capítol perquè el teu compte és d'un administrador.", "Vous êtes prêt\u001fYou’re ready": "Ja estàs llest/da", "Vérifier chiffres, dates et citations\u001fCheck figures, dates and quotes": "Comprova xifres, dates i citacions", "Zone de saisie\u001fMessage box": "Camp de missatge", "annonces\u001fannouncements": "avisos", "chapitres\u001fchapters": "capítols", "icône d’action\u001faction icon": "icona d'acció", "projet-alpha\u001fproject-alpha": "projecte-alfa", "« La date limite indiquée est le 15 mars, mais le site officiel indique le 31 mars. »\u001f“It says the deadline is 15 March, but the official site says 31 March.”": "'Diu que el termini és l'15 de març, però la pàgina oficial diu 31 de març'.", "« Nul. »\u001f“Bad.”": "\"Malbé.\"", "· Guide et tutoriels\u001f· Guide & tutorials": "· Guia & tutorials", "À faire\u001fDo": "Fes", "À gauche, tout pour naviguer entre vos conversations et espaces.\u001fOn the left, everything to move between chats and spaces.": "A l'esquerra, tot per moure entre xats i espais.", "À partir de la conversation jointe, rédige la version finale de l’e-mail.\u001fUsing the attached chat, write the final version of the email.": "Usant el xat adjunt, escriu la versió final del correu.", "À éviter\u001fAvoid": "Evitar", "Écoutez la réponse.\u001fListen to the answer.": "Escolta la resposta.", "Écrire une bonne demande, ajouter des documents, activer les outils, organiser vos projets, noter les réponses et utiliser l’IA de façon responsable.\u001fWriting good requests, adding documents, turning on tools, organizing projects, rating answers and using AI responsibly.": "Redactar bones peticióis, afegir documents, engegar eines, organitzar projectes, puntuar respostes i utilitzar IA de manera responsable.", "Écrivez librement. Sélectionnez un passage pour le faire réécrire par l’assistant.\u001fWrite freely. Select a passage to have the assistant rewrite it.": "Escriu lliurement. Selecciona un passatge perquè l'assistent el reescriu.", "Écrivez votre demande ici. Entrée pour envoyer, Maj + Entrée pour aller à la ligne.\u001fType your request here. Enter sends, Shift + Enter adds a new line.": "Escriu la teva petició aquí. Enter envia, Shift + Enter afegeix una línia.", "Écrivez, joignez un fichier avec +, mentionnez une personne ou un assistant.\u001fWrite, attach a file with +, mention a person or an assistant.": "Escriu, adjunta un fitxer amb +, menciona una persona o un assistent.", "Étape \u001fStep ": "Pas ", "Étiquettes\u001fTags": "Etiquetes", "Événements et rappels\u001fEvents and reminders": "Esdeveniments i recordatoris", "Événements, rappels, IA\u001fEvents, reminders, AI": "Esdeveniments, recordatoris, IA", "écran\u001fscreen": "pantalla", "écrans\u001fscreens": "pantalles", "⋯ › Pin to Sidebar pour la garder à portée.\u001f⋯ › Pin to Sidebar to keep it handy.": "⋯ › Fixa a Barra lateral per tenir-lo a mà.", "👍 / 👎 sous chaque réponse\u001f👍 / 👎 under each answer": "👍 / 👎 sota cada resposta", "👍 ouvre ce formulaire. Vos avis aident votre organisation à choisir et améliorer les assistants.\u001f👍 opens this form. Your ratings help your organization choose and improve assistants.": "👍 obre aquest formulari. Les teves puntuacions ajuden la teva organització a triar i millorar assistents.", "👎 ouvre la version négative. Un avis précis est bien plus utile qu’une simple note.\u001f👎 opens the negative version. A precise rating is far more useful than a score alone.": "👎 obre la versió negativa. Una puntuació precisa és molt més útil que una nota sola."}, "es": {" Le nombre, visible par les administrateurs, indique les utilisateurs actifs.\u001f The number, shown to administrators, is the count of active users.": " El número, visible para administradores, es el conteo de usuarios activos.", " résume les décisions de ce fil en 3 points.\u001f summarize the decisions in this thread in 3 points.": "resumir las decisiones en este hilo en 3 puntos.", " sur \u001f of ": " de ", "+ › Attach Knowledge › choisissez la base. Raccourci : tapez # dans la zone de saisie.\u001f+ › Attach Knowledge › pick the collection. Shortcut: type # in the message box.": "+ › Adjuntar conocimiento › elige la colección. Atajo: escribe # en la caja de mensaje.", "+ › Attach Webpage.\u001f+ › Attach Webpage.": "+ › Adjuntar página web.", "+ › Reference Chats › choisissez la conversation.\u001f+ › Reference Chats › pick the chat.": "+ › Chats de referencia › elegir el chat.", "1 = inutilisable, 5 = moyen.\u001f1 = unusable, 5 = average.": "1 = inutilizable, 5 = promedio.", "2 réponses\u001f2 replies": "2 respuestas", "Accueil\u001fHome": "Inicio", "Action ajoutée par votre organisation.\u001fAction added by your organization.": "Acción agregada por tu organización.", "Action sous les réponses\u001fAction under answers": "Acción bajo respuestas", "Activer des outils et fonctions pour cette conversation, comme la recherche web.\u001fTurn on tools and features for this chat, such as web search.": "Activar herramientas y funciones para este chat, como la búsqueda web.", "Activez la génération d’images.\u001fTurn on image generation.": "Activar generación de imágenes.", "Activez-le avant d’envoyer. Il reste actif pour la conversation.\u001fTurn it on before sending. It stays on for the conversation.": "Actívalo antes de enviar. Se mantiene activo para la conversación.", "Activé pour cette conversation seulement. Désactivez-le quand vous n’en avez plus besoin.\u001fOn for this conversation only. Turn it off when you no longer need it.": "Activado solo para esta conversación. Desactívalo cuando ya no lo necesites.", "Adapté à votre compte\u001fTailored to your account": "Personalizado para tu cuenta", "Admin Panel › Users › Groups pour les permissions.\u001fAdmin Panel › Users › Groups for permissions.": "Panel de administración › Usuarios › Grupos de permisos.", "Administrateurs\u001fAdministrators": "Administradores", "Administration\u001fAdministration": "Administración", "Affiche la note dans la barre latérale pour la retrouver vite et la glisser dans une conversation.\u001fShows the note in the sidebar to find it fast and drag it into a chat.": "Muestra la nota en la barra lateral para encontrarla rápido y arrastrarla a un chat.", "Affiche vos automatisations prévues et passées. En lecture seule : cliquez un événement pour ouvrir l’automatisation ou le chat produit.\u001fShows your planned and past automations. Read-only: click an event to open the automation or the chat it produced.": "Muestra tus automatizaciones programadas y pasadas. Solo lectura: haz clic en un evento para abrir la automatización o el chat que generó.", "Agenda personnel avec vues mois/semaine/jour, événements récurrents, rappels et partage.\u001fPersonal calendar with month/week/day views, recurring events, reminders and sharing.": "Calendario personal con vistas mensual/semanal/diaria, eventos repetitivos, recordatorios y compartir.", "Ajoute le texte d’une page à partir de son adresse.\u001fAdds a page’s text from its address.": "Añade el texto de una página a partir de su dirección.", "Ajoute une conversation passée comme contexte.\u001fAdds a past chat as context.": "Añade un chat anterior como contexto.", "Ajouter du contexte\u001fAdd context": "Agregar contexto", "Ajouter du contexte : fichiers, capture, page web, notes, documents, anciennes conversations.\u001fAdd context: files, capture, web page, notes, documents, past chats.": "Agregar contexto: archivos, captura, página web, notas, documentos, chats anteriores.", "Ajoutez un System Prompt et des documents.\u001fAdd a System Prompt and documents.": "Agregar un Prompt del Sistema y documentos.", "Ajoutez un emoji et un message court (« en réunion », « en cours »), visible par les autres dans les canaux.\u001fSet an emoji and a short message (“in a meeting”, “in class”), shown to others in channels.": "Establecer un emoji y un mensaje breve («en una reunión», «en clase»), visible para otros en los canales.", "Ajoutez un second modèle à côté du sélecteur.\u001fAdd a second model next to the selector.": "Agregar un segundo modelo junto al selector.", "Ajoutez une phrase précise, puis Save.\u001fAdd one precise sentence, then Save.": "Añade una oración precisa y luego guarda..", "Ajoutez, corrigez ou supprimez ce qui est retenu.\u001fAdd, edit or delete what is remembered.": "Añadir, editar o eliminar lo que se recuerda.", "Alerte avant l’événement (10 minutes par défaut) : notification dans la plateforme et dans le navigateur si vous l’avez autorisé.\u001fAlert before the event (10 minutes by default): in-app notification, and browser notification if allowed.": "Alerta antes del evento (10 minutos por defecto): notificación en la aplicación y notificación en el navegador si está permitido.", "All day\u001fAll day": "Todo el día", "Aller rapidement à une date.\u001fJump quickly to a date.": "Saltar rápidamente a una fecha.", "Améliorer, résumer, extraire les actions, réécrire la sélection : l’assistant modifie la note directement.\u001fEnhance, summarize, extract action items, rewrite selection: the assistant edits the note directly.": "Mejorar, resumir, extraer acciones pendientes, reescribir selección: el asistente edita la nota directamente.", "Analysez vos documents : résumé, extraction, traduction, comparaison.\u001fAnalyze your documents: summary, extraction, translation, comparison.": "Analiza tus documentos: resumen, extracción, traducción, comparación.", "Annuler / rétablir\u001fUndo / redo": "Deshacer / rehacer", "Après 👍, seules les notes hautes sont disponibles. 10 = parfaite.\u001fAfter 👍, only high scores are available. 10 = perfect.": "Después de 👍, solo están disponibles las puntuaciones altas. 10 = perfecto.", "Assistant actif\u001fActive assistant": "Asistente activo", "Assistants\u001fAssistants": "Asistentes", "Astuce : maintenez Maj (Shift) dans ce menu pour épingler Calendar ou Automations dans la barre latérale.\u001fTip: hold Shift in this menu to pin Calendar or Automations to the sidebar.": "Tip: mantén Shift en este menú para fijar Calendario o Automatizaciones en la barra lateral.", "Attendez la fin du chargement.\u001fWait for the upload to finish.": "Espera a que termine la subida.", "Aucune synchronisation avec Outlook ou Google Agenda.\u001fNo sync with Outlook or Google Calendar.": "Sin sincronización con Outlook o Google Calendar.", "Automations › Create.\u001fAutomations › Create.": "Automatizaciones › Crear.", "Automatisations\u001fAutomations": "Automatizaciones", "Avant chaque action, l’assistant affiche ce qu’il veut faire et attend votre accord. Recommandé pour tout ce qui envoie, modifie ou supprime.\u001fBefore each action, the assistant shows what it wants to do and waits for your approval. Recommended for anything that sends, changes or deletes.": "Antes de cada acción, el asistente muestra lo que quiere hacer y espera tu aprobación. Recomendado para todo lo que envía, cambia o elimina.", "Avantages et limites\u001fPros and cons": "Ventajas e inconvenientes", "Avis\u001fFeedback": "Feedback", "Avis peu utile\u001fUnhelpful feedback": "Feedback no útil", "Avis sur les réponses\u001fRating answers": "Valoración de respuestas", "Avis utile\u001fHelpful feedback": "Feedback útil", "Barre latérale\u001fSidebar": "Barra lateral", "Barre latérale › Notes. Un espace pour rédiger des textes qui durent : comptes rendus, plans, idées.\u001fSidebar › Notes. A place for lasting writing: minutes, outlines, ideas.": "Barra lateral › Notas. Un espacio para escritura duradera: actas, esquemas, ideas.", "Bases de documents\u001fKnowledge": "Conocimiento", "Bienvenue ! Ce guide interactif vous apprend à utiliser la plateforme, écran par écran. Il montre uniquement les fonctions disponibles pour votre compte.\u001fWelcome! This interactive guide teaches you how to use the platform, screen by screen. It only shows the features available on your account.": "¡Bienvenido! Esta guía interactiva te enseña a usar la plataforma, pantalla por pantalla. Solo muestra las características disponibles en tu cuenta.", "Bienvenue sur \u001fWelcome to ": "¡Bienvenido a ", "Bon usage\u001fGood practice": "Buena práctica", "Bonne réponse\u001fGood response": "Buena respuesta", "Bouton rond (zone vide) : conversation orale.\u001fRound button (empty box): spoken conversation.": "Botón redondo (caja vacía): conversación hablada.", "Calcule la moyenne et l’écart-type par groupe et trace un histogramme.\u001fCompute mean and standard deviation per group and plot a histogram.": "Calcular la media y la desviación estándar por grupo y trazar un histograma.", "Calculs, données, graphiques\u001fCalculations, data, charts": "Cálculos, datos, gráficos", "Calendrier\u001fCalendar": "Calendario", "Canaux\u001fChannels": "Canales", "Ce guide vous montre l’interface réelle, élément par élément. Cliquez sur un élément de l’écran ou sur son explication pour le mettre en évidence.\u001fThis guide walks you through the real interface, one element at a time. Click any element on the screen or its explanation to highlight it.": "Esta guía te acompaña por la interfaz real, elemento a elemento. Haz clic en cualquier elemento de la pantalla o su explicación para resaltarlo.", "Ce que l’assistant fait seul\u001fWhat the assistant does on its own": "Lo que el asistente hace por su cuenta", "Ce que vous allez apprendre\u001fWhat you will learn": "Lo que aprenderás", "Ces outils ne fonctionnent qu’avec les assistants configurés pour eux et selon vos permissions. Si rien ne se passe, choisissez un autre assistant.\u001fThese tools only work with assistants set up for them and within your permissions. If nothing happens, choose another assistant.": "Estas herramientas solo funcionan con asistentes configurados para ellas y dentro de tus permisos. Si nada ocurre, elige otro asistente.", "Cette ligne montre l’outil lancé. Cliquez dessus pour voir ce qui a été envoyé et reçu.\u001fThis line shows the tool that ran. Click it to see what was sent and received.": "Esta línea muestra la herramienta que se ejecutó. Haz clic en ella para ver qué se envió y recibió.", "Cette liste s’adapte à votre compte. Ouvrez une fiche pour savoir où la trouver et comment bien l’utiliser.\u001fThis list adapts to your account. Open a card to see where to find it and how to use it well.": "Esta lista se adapta a tu cuenta. Abre una tarjeta para ver dónde encontrarla y cómo usarla bien.", "Change la vue : mois, semaine ou jour.\u001fSwitch view: month, week or day.": "Cambiar vista: mensual, semanal o diaria.", "Changez d’assistant pour voir d’autres suggestions.\u001fSwitch assistants to see other suggestions.": "Cambiar de asistentes para ver otras sugerencias.", "Chapitres\u001fChapters": "Capítulos", "Chaque exécution avec son statut (success ou error) et le lien vers la conversation créée.\u001fEach run with its status (success or error) and a link to the chat created.": "Cada ejecución con su estado (éxito o error) y un enlace al chat creado.", "Chaque jour à 18:00\u001fEvery day at 18:00": "Todos los días a las 18:00", "Chaque lundi à 08:00 · prochaine : 21 sept.\u001fEvery Monday at 08:00 · next: 21 Sep": "Todos los lunes a las 08:00 · siguiente: 21 Sep", "Chaque lundi, recherche les nouveautés publiées sur ce sujet et présente-les dans un tableau avec lien, source et date.\u001fEvery Monday, find what was published on this topic and list it in a table with link, source and date.": "Todos los lunes, buscar qué se publicó sobre este tema e incluirlo en una tabla con enlace, fuente y fecha.", "Cherche dans les canaux dont vous êtes membre.\u001fSearches channels you belong to.": "Busca en los canales al que perteneces.", "Cherche et lit des pages quand Web Search est activé.\u001fSearches and reads pages when Web Search is on.": "Busca y lee páginas cuando la Búsqueda web está activada.", "Cherche sur Internet et cite ses sources. À activer pour toute information récente : actualités, dates, règlements.\u001fSearches the internet and cites sources. Turn on for anything recent: news, dates, regulations.": "Busca en internet y cita las fuentes. Activa para todo lo reciente: noticias, fechas, reglamentos.", "Choisir le bon assistant\u001fChoosing the right assistant": "Elegir el asistente adecuado", "Choisir l’assistant\u001fChoosing the assistant": "Elegir el asistente", "Choisir l’assistant ou le modèle qui répond.\u001fChoose the assistant or model that answers.": "Elegir el asistente o modelo que responde.", "Choisissez ce qui était vraiment bien : exactitude, respect des consignes, clarté…\u001fPick what was actually good: accuracy, following instructions, clarity…": "Elegir qué fue realmente bueno: precisión, seguimiento de instrucciones, claridad…", "Choisissez la commande et complétez les champs.\u001fPick the command and fill in the fields.": "Elegir el comando y rellenar los campos.", "Choisissez un modèle, un prompt système et envoyez.\u001fPick a model, a system prompt and send.": "Elegir un modelo, un prompt de sistema y enviar.", "Choisissez une note et une raison.\u001fPick a score and a reason.": "Elegir una puntuación y una razón.", "Clair et professionnel\u001fClear and professional": "Claro y profesional", "Classez l’avis par sujet, par exemple « recherche » ou « rédaction ».\u001fCategorize by topic, e.g. “search” or “writing”.": "Categorizar por tema, por ejemplo «búsqueda» o «redacción».", "Cliquez pour le modifier ou le supprimer.\u001fClick to edit or delete it.": "Haz clic para editar o borrarlo.", "Cliquez pour l’appliquer à cette réponse.\u001fClick to apply it to this answer.": "Haz clic para aplicarlo a esta respuesta.", "Cliquez pour ouvrir. ⋯ pour les options.\u001fClick to open. ⋯ for options.": "Haz clic para abrir. ⋯ para opciones.", "Cliquez pour renommer la note.\u001fClick to rename the note.": "Haz clic para renombrar la nota.", "Cliquez sur + pour créer un dossier. Glissez-déposez ensuite des conversations dedans.\u001fClick + to create a folder. Then drag chats into it.": "Haz clic + para crear una carpeta. Luego arrastra los chats hacia ella.", "Cliquez sur + pour ouvrir ce menu. Les lignes avec une flèche › ouvrent une seconde liste : cliquez dessus ici pour la voir.\u001fClick + to open this menu. Rows with an arrow › open a second list: click them here to see it.": "Haz clic + para abrir este menú. Las filas con una flecha › abren una segunda lista: haz clic en ellas aquí para verla.", "Cliquez sur + › Upload Files, ou glissez le fichier dans la zone de saisie.\u001fClick + › Upload Files, or drag the file into the message box.": "Haz clic + › Subir archivos, o arrastra el archivo hacia la caja de mensajes.", "Cliquez sur le nom à droite de la zone de saisie pour ouvrir la liste.\u001fClick the name on the right of the message box to open the list.": "Haz clic en el nombre a la derecha de la caja de mensajes para abrir la lista.", "Cliquez sur l’icône de l’action.\u001fClick the action icon.": "Haz clic en el icono de acción.", "Cliquez sur l’écran ou sur une explication\u001fClick the screen or an explanation": "Haz clic en la pantalla o una explicación", "Cliquez sur une colonne pour trier.\u001fClick a column to sort.": "Haz clic en una columna para ordenar.", "Cliquez sur une suggestion : elle remplit la zone de saisie. Complétez-la avec vos détails avant d’envoyer.\u001fClick a suggestion: it fills the message box. Add your details before sending.": "Haz clic en una sugerencia: rellena la caja de mensajes. Añade tus detalles antes de enviar.", "Cliquez sur votre nom en bas de la barre latérale pour ouvrir ce menu. Le point vert indique que vous êtes en ligne.\u001fClick your name at the bottom of the sidebar to open this menu. The green dot means you are online.": "Haz clic en tu nombre en la parte inferior de la barra lateral para abrir este menú. El punto verde indica que estás en línea.", "Cliquez sur votre nom en bas de la barre latérale.\u001fClick your name at the bottom of the sidebar.": "Haz clic en tu nombre en la parte inferior de la barra lateral.", "Cliquez sur votre nom.\u001fClick your name.": "Haz clic en tu nombre.", "Cliquez 👍 ou 👎.\u001fClick 👍 or 👎.": "Haz clic 👍 o 👎.", "Coller l’adresse d’une page web : son texte est lu et ajouté comme contexte.\u001fPaste a web page address: its text is read and added as context.": "Pegar una dirección de página web: su texto se lee y se añade como contexto.", "Collez l’adresse complète (https://…).\u001fPaste the full address (https://…).": "Pegar la dirección completa (https://…)", "Commencer\u001fGet started": "Empezar", "Commencer la visite\u001fStart the tour": "Iniciar el recorrido", "Comment l’utiliser\u001fHow to use it": "Cómo usarlo", "Compare ces deux documents et liste les différences dans un tableau.\u001fCompare these two documents and list the differences in a table.": "Compara estos dos documentos e incluye las diferencias en una tabla.", "Comparer des modèles\u001fCompare models": "Comparar modelos", "Comparer deux options\u001fCompare two options": "Comparar dos opciones", "Compte rendu de réunion\u001fMeeting minutes": "Actas de reunión", "Compteur\u001fCounter": "Contador", "Confidentialité\u001fPrivacy": "Privacidad", "Connaît la date et calcule des délais : « dans 3 semaines », « lundi prochain ».\u001fKnows the date and computes delays: “in 3 weeks”, “next Monday”.": "Conoce la fecha y calcula los retrasos: «en 3 semanas», «el próximo lunes».", "Consignes appliquées à toutes les conversations du dossier : rôle, ton, format. Visible seulement si votre compte l’autorise.\u001fInstructions applied to every chat in the folder: role, tone, format. Only shown if your account allows it.": "Instrucciones aplicadas a cada chat de la carpeta: rol, tono, formato. Solo visible si tu cuenta lo permite.", "Continuer\u001fContinue": "Continuar", "Conversation\u001fConversation": "Conversación", "Conversations\u001fChats": "Chats", "Copie la réponse avec sa mise en forme.\u001fCopies the answer with its formatting.": "Copia la respuesta con su formato.", "Copier\u001fCopy": "Copiar", "Copier un lien ou partager la note.\u001fCopy a link or share the note.": "Copiar un enlace o compartir la nota.", "Copier une réponse sans la relire\u001fCopying an answer without reading it": "Copiar una respuesta sin leerla", "Corrige le texte de la réponse, par exemple avant de la copier.\u001fEdit the answer text, for example before copying it.": "Editar el texto de la respuesta, por ejemplo antes de copiarlo.", "Crée le dossier. Vous pourrez modifier ces réglages plus tard.\u001fCreates the folder. You can change these settings later.": "Crea la carpeta. Luego puedes cambiar estas opciones después.", "Crée un événement. Vous pouvez aussi cliquer directement sur un jour de la grille.\u001fCreates an event. You can also click a day on the grid.": "Crea un evento. También puedes hacer clic en un día en la cuadrícula.", "Crée une image à partir de votre description.\u001fCreates an image from your description.": "Crea una imagen desde tu descripción.", "Crée une image à partir d’une description.\u001fCreates an image from a description.": "Genera una imagen desde una descripción.", "Crée une note. La flèche propose d’autres options de création.\u001fCreates a note. The arrow offers other creation options.": "Crea una nota. La flecha ofrece otras opciones de creación.", "Crée, déplace ou supprime des événements et consulte votre semaine.\u001fCreates, moves or deletes events and checks your week.": "Crea, mueve o elimina eventos y comprueba tu semana.", "Crée, liste, met en pause ou supprime une tâche planifiée.\u001fCreates, lists, pauses or deletes a scheduled task.": "Crea, enumera, pausa o elimina una tarea programada.", "Créer et gérer assistants, bases de documents, modèles de demandes, selon vos droits.\u001fCreate and manage assistants, document collections and prompt templates, depending on your rights.": "Crear y gestionar asistentes, colecciones de documentos y plantillas de prompts, según tus derechos.", "Créer et gérer des assistants, documents ou modèles de demandes, selon vos droits.\u001fCreate and manage assistants, documents or prompt templates, depending on your rights.": "Crear y gestionar asistentes, documentos o plantillas de prompts, según tus derechos.", "Créer et suivre une automatisation\u001fCreating and monitoring an automation": "Crear y supervisar una automatización", "Créer un canal (selon vos droits) et choisir qui y a accès.\u001fCreate a channel (if allowed) and choose who can access it.": "Crear un canal (si está permitido) y elegir quién puede acceder a él.", "Créer un dossier pas à pas\u001fCreate a folder step by step": "Crear una carpeta paso a paso", "Créer un événement\u001fCreating an event": "Crear un evento", "Créer une illustration\u001fCreate an illustration": "Crear una ilustración", "Créez d’autres calendriers (« Équipe », « Projet ») avec leur couleur.\u001fCreate more calendars (“Team”, “Project”) with their own colour.": "Crear más calendarios («Equipo», «Proyecto») con su propio color.", "Créé automatiquement : votre calendrier par défaut. Cliquez pour l’afficher ou le masquer.\u001fCreated automatically: your default calendar. Click to show or hide it.": "Creado automáticamente: tu calendario predeterminado. Haz clic para mostrarlo u ocultarlo.", "C’est noté : événement créé vendredi à 15:00, rappel 10 minutes avant.\u001fDone: event created on Friday at 15:00 with a reminder 10 minutes before.": "Hecho: evento creado el viernes a las 15:00 con un recordatorio 10 minutos antes.", "Dans + › Tool Permissions, décidez du niveau de contrôle quand l’assistant utilise un outil.\u001fIn + › Tool Permissions, decide how much control you keep when the assistant uses a tool.": "En + › Permisos de herramientas, decides cuánto control mantienes cuando el asistente usa una herramienta.", "Dans la conversation\u001fIn the chat": "En el chat", "Dans quel calendrier ranger l’événement.\u001fWhich calendar to put it in.": "¿En qué calendario ponerlo.", "Date de modification, nombre de mots et de caractères.\u001fLast edit, word and character count.": "Última edición, conteo de palabras y caracteres.", "Date et heure\u001fDate & time": "Fecha & hora", "Date et heure, recherche web, documents, mémoire, notes, anciennes conversations, calendrier, automatisations, images, code, listes de tâches : selon l’assistant et vos droits.\u001fDate & time, web search, documents, memory, notes, past chats, calendar, automations, images, code, task lists: depending on the assistant and your rights.": "Fecha & hora, búsqueda web, documentos, memoria, notas, chats previos, calendario, automatizaciones, imágenes, código, listas de tareas: depende del asistente y tus derechos.", "Date et heures, ou journée entière.\u001fDate and times, or all day.": "Fecha y horas, o todo el día.", "Demandes planifiées\u001fScheduled requests": "Solicitudes programadas", "Demandez ce que vous voulez en citant le fichier.\u001fAsk what you need, referring to the file.": "Pregunta lo que necesitas, haciendo referencia al archivo.", "Demandez de continuer ou de réutiliser ce qui a été produit.\u001fAsk to continue or reuse what was produced.": "Pide que continúe o reutilice lo que se ha producido.", "Demandez en langage naturel.\u001fAsk in plain language.": "Pregunta en lenguaje sencillo.", "Demandez les sources utilisées.\u001fAsk for the sources used.": "Pregunta por las fuentes utilizadas.", "Des demandes qui s’exécutent toutes seules à heure fixe. Voir chapitre Automatisations.\u001fRequests that run on their own on a schedule. See the Automations chapter.": "Solicitudes que se ejecutan solas en una programación. Ver el capítulo Automatizaciones.", "Des documents persistants avec un assistant qui peut les modifier directement.\u001fLasting documents with an assistant that can edit them directly.": "Documentos duraderos con un asistente que puede editarlos directamente.", "Des idées de questions prêtes à l’emploi. Cliquez sur l’une d’elles pour la placer dans la zone de saisie.\u001fReady-made question ideas. Click one to place it in the message box.": "Ideas de preguntas ya preparadas. Haz clic en una para colocarla en la caja de mensajes.", "Des outils connectés permettent à l’assistant de consulter ou agir sur des services.\u001fConnected tools let the assistant look up or act on services.": "Las herramientas conectadas permiten al asistente buscar o actuar sobre servicios.", "Dicter, écouter, parler\u001fDictate, listen, talk": "Dictado, escuchar, hablar", "Dictez ou enregistrez de l’audio : le texte est ajouté à la note.\u001fDictate or record audio: the text is added to the note.": "Dictar o grabar audio: el texto se añade a la nota.", "Dictée\u001fDictation": "Dictado", "Dictée avec le micro, mode vocal avec le bouton rond, lecture des réponses avec l’icône haut-parleur.\u001fDictation with the mic, voice mode with the round button, read-aloud with the speaker icon.": "Dictado con el micrófono, modo voz con el botón redondo, lectura en voz alta con el icono de altavoz.", "Discussions d’équipe avec l’IA\u001fTeam discussions with AI": "Discusiones en equipo con IA", "Documents\u001fKnowledge": "Conocimiento", "Documents confidentiels non autorisés\u001fUnauthorized confidential documents": "Documentos confidenciales sin autorización", "Donner un avis positif\u001fGiving positive feedback": "Dar feedback positivo", "Donner votre avis\u001fGive feedback": "Dar feedback", "Donner votre avis sur les réponses\u001fRate the answers": "Valorar las respuestas", "Données personnelles de tiers\u001fOther people’s personal data": "Datos personales de otras personas", "Dossiers\u001fFolders": "Carpetas", "Déconnectez-vous sur un ordinateur partagé.\u001fSign out on a shared computer.": "Cerrar sesión en una computadora compartida.", "Déconnexion. Indispensable sur un ordinateur partagé.\u001fSigns you out. Essential on a shared computer.": "Te cierra la sesión. Esencial en una computadora compartida.", "Décrivez le calcul ou le graphique attendu.\u001fDescribe the calculation or chart you want.": "Describe el cálculo o gráfico que deseas.", "Décrivez sujet, style, cadrage et format.\u001fDescribe subject, style, framing and format.": "Describir asunto, estilo, encuadre y formato.", "Définit si l’assistant peut utiliser les outils librement (Full access) ou doit vous demander avant chaque action (Ask for approval). La flèche ouvre ce choix.\u001fSets whether the assistant can use tools freely (Full access) or must ask before each action (Ask for approval). The arrow opens this choice.": "Establece si el asistente puede usar herramientas libremente (Acceso total) o debe preguntar antes de cada acción (Pedir aprobación). La flecha abre esta opción.", "Démarre une conversation vide. Changez de conversation quand vous changez de sujet.\u001fStarts an empty chat. Start a new one when you change topic.": "Inicia un chat vacío. Inicia uno nuevo cuando cambies de tema.", "Démarrer\u001fStart": "Empezar", "Démarrez vos conversations depuis le dossier.\u001fStart chats from the folder.": "Iniciar chats desde la carpeta.", "Désactivez-la quand l’information récente n’est pas nécessaire.\u001fTurn it off when recent information is not needed.": "Desactívalo cuando no necesites información reciente.", "Détails\u001fDetails": "Detalles", "Détails de génération : durée, longueur.\u001fGeneration details: time, length.": "Detalles de generación: tiempo, longitud.", "E-mail au fournisseur\u001fEmail to supplier": "Correo electrónico al proveedor", "En 5 points\u001fIn 5 points": "En 5 puntos", "En cas de doute, choisissez Ask for approval : vous gardez la main sur chaque action.\u001fIf in doubt, choose Ask for approval: you stay in control of every action.": "Si hay duda, elige Pedir aprobación: mantienes el control de cada acción.", "En pause\u001fPaused": "Pausado", "En violet : une automatisation planifiée.\u001fIn purple: a scheduled automation.": "En color morado: una automatización programada.", "Enregistre l’avis. Il n’est pas visible par les autres utilisateurs.\u001fSaves the rating. It is not visible to other users.": "Guarda la valoración. No es visible para otros usuarios.", "Enregistre l’événement.\u001fSaves the event.": "Guarda el evento.", "Enregistrer\u001fRecord": "Grabar", "Enregistrez, puis reformulez ou régénérez pour obtenir une meilleure réponse.\u001fSave, then rephrase or regenerate for a better answer.": "Guardar, luego reformular o regenerar para una mejor respuesta.", "Environ 10 minutes\u001fAbout 10 minutes": "Acerca de 10 minutos", "Envoyer / mode vocal\u001fSend / voice mode": "Enviar / modo de voz", "Envoyer un fichier depuis votre ordinateur : PDF, Word, Excel, image. Vous pouvez aussi le glisser dans la zone de saisie.\u001fSend a file from your computer: PDF, Word, Excel, image. You can also drag it into the message box.": "Enviar un archivo desde tu computadora: PDF, Word, Excel, imagen. También puedes arrastrarlo a la caja de mensajes.", "Envoyez une seule fois : chaque modèle répond.\u001fSend once: each model answers.": "Enviar una vez: cada modelo responde.", "Espace administrateur\u001fAdministrator area": "Administración", "Espaces de discussion partagés. Le + crée un canal si vous en avez le droit.\u001fShared discussion spaces. The + creates a channel if you are allowed.": "Espacios de discusión compartidos. El + crea un canal si tienes permiso.", "Espaces partagés où l’IA répond quand on la mentionne.\u001fShared spaces where AI answers when mentioned.": "Espacios compartidos donde la IA responde cuando se menciona.", "Essayer dans le chat\u001fTry it in the chat": "Probarlo en el chat", "Ex. : Tu es un tuteur de statistiques. Réponds avec des exemples simples.\u001fe.g. You are a statistics tutor. Answer with simple examples.": "por ejemplo. Eres mi tutor de estadísticas. Responde con ejemplos sencillos.", "Exemple\u001fExample": "Ejemplo", "Exemple ajouté dans la zone de saisie.\u001fExample added to the message box.": "Ejemplo añadido a la caja de mensajes.", "Exemple de System Prompt\u001fExample System Prompt": "Prompt de sistema de ejemplo", "Exemple d’instructions\u001fExample instructions": "Instrucciones de ejemplo", "Exemples : « Projet Alpha » avec ses documents, « Rapport annuel » avec le guide de rédaction, « Candidatures » avec votre CV.\u001fExamples: “Project Alpha” with its documents, “Annual report” with the writing guide, “Applications” with your CV.": "Ejemplos: «Proyecto Alpha» con sus documentos, «Informe anual» con la guía de redacción, «Solicitudes» con tu CV.", "Exporter la note (texte, Markdown ou PDF).\u001fExport the note (text, Markdown or PDF).": "Exportar la nota (texto, Markdown o PDF).", "Extrais les actions de cette note avec responsable et échéance.\u001fExtract action items from this note with owner and deadline.": "Extraer acciones pendientes de esta nota con responsable y fecha límite.", "Exécute du code pour calculer ou analyser des données.\u001fRuns code to calculate or analyze data.": "Ejecuta código para calcular o analizar datos.", "Exécute du code pour calculer, analyser un fichier de données ou produire un graphique.\u001fRuns code to calculate, analyze a data file or build a chart.": "Ejecuta código para calcular, analizar un archivo de datos o construir un gráfico.", "Exécute une demande automatiquement (une fois, chaque heure, jour, semaine, mois). Chaque exécution crée une conversation.\u001fRuns a request automatically (once, hourly, daily, weekly, monthly). Each run creates a chat.": "Ejecuta una solicitud automáticamente (una vez, por hora, diario, semanal, mensual). Cada ejecución crea un chat.", "Exécution automatique\u001fAutomation run": "Ejecución de la automatización", "Exécution de code\u001fCode execution": "Ejecución de código", "Fermer\u001fClose": "Cerrar", "Fermez le guide et posez votre première question. L’onglet Fonctions reste disponible pour revoir chaque fonctionnalité.\u001fClose the guide and ask your first question. The Features tab stays available to revisit every feature.": "Cierra la guía y haz tu primera pregunta. La pestaña Funciones permanece disponible para volver a ver cada función.", "Fichiers\u001fFiles": "Archivos", "Fils de discussion\u001fThreads": "Hilos", "Filtre de statut\u001fStatus filter": "Filtro de estado", "Filtrer (toutes, les vôtres…), le mode d’ouverture et l’affichage liste ou grille.\u001fFilter (all, yours…), open mode, and list or grid view.": "Filtrado (todos, tuyos…), modo abierto, y vista de lista o cuadrícula.", "Folders › + ouvre cette fenêtre.\u001fFolders › + opens this window.": "Carpetas › + abre esta ventana.", "Folders › +, donnez un nom.\u001fFolders › +, give a name.": "Carpetas › +, asignar un nombre.", "Fonction ajoutée par votre organisation. L’interrupteur l’active pour cette conversation.\u001fA feature added by your organization. The switch turns it on for this chat.": "Una función agregada por tu organización. El interruptor lo activa para este chat.", "Fonctions\u001fFeatures": "Funciones", "Fonctions intégrées\u001fBuilt-in features": "Características integradas", "Gardez les décisions et liens importants en haut.\u001fKeep key decisions and links at the top.": "Mantener decisiones clave y enlaces en la parte superior.", "Guide des outils\u001fTools handbook": "Manual de herramientas", "Guide d’utilisation de \u001fHow to use ": "Cómo usar ", "Guide fermé\u001fGuide closed": "Guía cerrada", "Guide mis à jour. \u001fGuide updated. ": "Guía actualizada.", "Génère une image à partir de la conversation.\u001fGenerates an image from the conversation.": "Genera una imagen desde la conversación.", "Génération d’images\u001fImage generation": "Generación de imágenes", "Icône d’information\u001fInfo icon": "Icono de información", "Idées de projet\u001fProject ideas": "Ideas de proyecto", "Idées et inspiration\u001fIdeas & inspiration": "Ideas & inspiración", "Il donne accès à vos espaces personnels et à vos réglages. Son contenu dépend de votre rôle.\u001fIt opens your personal spaces and settings. What you see depends on your role.": "Abre tus espacios personales y configuraciones. Lo que ves depende de tu rol.", "Illustration simple et moderne d’une équipe en réunion, format 16:9, sans texte.\u001fSimple modern illustration of a team in a meeting, 16:9, no text.": "Ilustración moderna simple de un equipo en una reunión, 16:9, sin texto.", "Images\u001fImages": "Imágenes", "Indiquez ce qui était faux et, si possible, la bonne information.\u001fSay what was wrong and, if you can, the correct information.": "Indicar qué estaba mal y, si puedes, la información correcta.", "Informations\u001fInfo": "Información", "Informations à jour avec sources\u001fUp-to-date info with sources": "Información actualizada con fuentes", "Insère une demande préparée à l’avance.\u001fInserts a request prepared in advance.": "Inserta una solicitud preparada con antelación.", "Interroger des documents\u001fAsk document collections": "Pregunta sobre colecciones de documentos", "Interrupteur\u001fSwitch": "Cambiar", "Interrupteur d’outil\u001fTool switch": "Interruptor de herramienta", "Intégrations\u001fIntegrations": "Integraciones", "Intégrations › Tools › activez l’outil utile.\u001fIntegrations › Tools › turn on the tool you need.": "Integraciones › Herramientas › activa la herramienta que necesitas.", "Intégrations › Tools › ouvre la liste. Activez l’outil, puis demandez simplement ce que vous voulez : l’assistant l’utilisera au bon moment.\u001fIntegrations › Tools › opens the list. Turn the tool on, then just ask: the assistant uses it when needed.": "Integraciones › Herramientas › abre la lista. Activa la herramienta, luego simplemente pregunta: el asistente la usa cuando sea necesario.", "Intégrations › Web Search. Posez ensuite votre question : la réponse affiche les sources consultées.\u001fIntegrations › Web Search. Then ask your question: the answer shows the sources it used.": "Integraciones › Búsqueda web. Luego haz tu pregunta: la respuesta muestra las fuentes que utilizó.", "Intégrations › activez Code Interpreter.\u001fIntegrations › turn on Code Interpreter.": "Integraciones › activar Código interpretador.", "Intégrations › activez Web Search.\u001fIntegrations › turn on Web Search.": "Integraciones › activar Búsqueda web.", "Joignez un fichier CSV ou Excel si besoin.\u001fAttach a CSV or Excel file if needed.": "Adjunta un archivo CSV o Excel si es necesario.", "Joindre des fichiers à la note comme contexte pour l’assistant.\u001fAttach files to the note as context for the assistant.": "Adjunta archivos a la nota como contexto para el asistente.", "J’ai déposé le plan du rapport, vos avis ?\u001fI shared the report outline, thoughts?": "Compartí el esquema del informe, ¿qué opinas?", "La barre latérale\u001fThe sidebar": "La barra lateral", "La demande envoyée à chaque exécution. Soyez complet : elle doit fonctionner sans vous.\u001fThe request sent on each run. Be complete: it must work without you.": "La solicitud enviada en cada ejecución. Sea completa: debe funcionar sin ti.", "La flèche liste les bases de documents auxquelles vous avez accès. L’assistant cherche la réponse dedans.\u001fThe arrow lists document collections you can access. The assistant searches them for the answer.": "La flecha lista las colecciones de documentos a las que puedes acceder. El asistente las busca para la respuesta.", "La flèche liste vos Notes. Le contenu complet de la note est ajouté au message.\u001fThe arrow lists your Notes. The full note content is added to the message.": "La flecha lista tus Notas. El contenido completo de la nota se añade al mensaje.", "La flèche liste vos anciennes conversations pour vous appuyer sur un échange précédent.\u001fThe arrow lists your past chats so you can build on an earlier exchange.": "La flecha lista tus chats anteriores para que puedas basarte en un intercambio anterior.", "La flèche ouvre la liste des fichiers que vous avez déjà envoyés, pour les réutiliser sans les téléverser à nouveau.\u001fThe arrow opens files you already uploaded, so you can reuse them without uploading again.": "La flecha abre los archivos que ya subiste, para que puedas reutilizarlos sin subirlos de nuevo.", "La flèche à gauche revient au menu Intégrations.\u001fThe left arrow returns to the Integrations menu.": "La flecha izquierda vuelve al menú de Integraciones.", "La flèche › ouvre la liste de vos outils. Activez uniquement ceux utiles à la conversation en cours.\u001fThe arrow › opens your tools. Turn on only the ones this conversation needs.": "La flecha › abre tus herramientas. Activa solo las que necesita esta conversación.", "La page Notes\u001fThe Notes page": "La página de Notas", "Lance immédiatement pour tester avant d’attendre l’horaire.\u001fRuns immediately to test before the schedule.": "Ejecuta inmediatamente para probar antes de la programación.", "Langue, thème, compte\u001fLanguage, theme, account": "Idioma, tema, cuenta", "Langue, thème, notifications, voix, personnalisation et mémoire.\u001fLanguage, theme, notifications, voice, personalization and memory.": "Idioma, tema, notificaciones, voz, personalización y memoria.", "Le Calendrier\u001fThe Calendar": "El calendario", "Le bouton + : ajouter du contexte\u001fThe + button: add context": "El botón +: añadir contexto", "Le bouton + et ses menus\u001fThe + button and its menus": "El botón + y sus menús", "Le bouton Intégrations\u001fThe Integrations button": "El botón de Integraciones", "Le bouton à côté de + regroupe les outils et fonctions. Chaque interrupteur s’applique à la conversation en cours.\u001fThe button next to + groups tools and features. Each switch applies to the current conversation.": "El botón junto a + agrupa herramientas y funciones. Cada interruptor se aplica a la conversación actual.", "Le calendrier ne se synchronise pas avec Outlook ou Google Agenda.\u001fThe calendar does not sync with Outlook or Google Calendar.": "El calendario no se sincroniza con Outlook ni Google Calendar.", "Le guide a été enrichi. Parcourez le sommaire pour voir les nouveautés.\u001fThe guide has been updated. Browse the contents to see what is new.": "La guía ha sido actualizada. Recorre el contenido para ver qué es nuevo.", "Le menu utilisateur\u001fThe user menu": "El menú del usuario", "Le menu ⋯ d’une note\u001fThe note ⋯ menu": "El menú de la nota ⋯", "Le nom en haut indique l’assistant qui va répondre.\u001fThe name at the top shows which assistant will answer.": "El nombre de arriba muestra qué asistente responderá.", "Les actions sous chaque réponse\u001fActions under each answer": "Acciones bajo cada respuesta", "Les automatisations\u001fAutomations": "Automatizaciones", "Les canaux : travailler à plusieurs avec l’IA\u001fChannels: working together with AI": "Canales: trabajando junto con la IA", "Les canaux dont vous êtes membre. Un point signale les messages non lus.\u001fChannels you belong to. A dot marks unread messages.": "Canales al que perteneces. Un punto marca los mensajes no leídos.", "Les commandes de la zone de saisie\u001fThe message box controls": "Los controles de la caja de mensajes", "Les dernières icônes sont des actions propres à votre compte. Survolez-les pour voir leur nom.\u001fThe last icons are actions specific to your account. Hover them to see their name.": "Los últimos iconos son acciones específicas de tu cuenta. Pasa el cursor sobre ellos para ver su nombre.", "Les outils intégrés de l’assistant\u001fThe assistant’s built-in tools": "Las herramientas integradas del asistente", "Les questions suggérées\u001fSuggested questions": "Preguntas sugeridas", "Les suggestions changent selon l’assistant choisi : elles montrent ce qu’il sait bien faire.\u001fSuggestions change with the selected assistant: they show what it does best.": "Las sugerencias cambian según el asistente seleccionado: muestran en qué es mejor.", "Lire une page web\u001fRead a web page": "Leer una página web", "Lire à voix haute\u001fRead aloud": "Leer en voz alta", "Liste de tâches\u001fTask list": "Lista de tareas", "Liste des canaux\u001fChannel list": "Lista de canales", "Liées à l’assistant\u001fTied to the assistant": "Unido al asistente", "L’assistant agit pour vous\u001fThe assistant acts for you": "El asistente actúa por ti", "L’assistant cherche la réponse dans une collection de documents et cite les passages.\u001fThe assistant searches a document collection and cites passages.": "El asistente busca en una colección de documentos y cita los pasajes.", "L’assistant lance les outils dont il a besoin sans vous interrompre. Idéal pour les recherches et lectures.\u001fThe assistant runs the tools it needs without interrupting you. Best for searches and reading.": "El asistente ejecuta las herramientas que necesita sin interrumpirte. Ideal para búsquedas y lecturas.", "L’assistant peut se tromper avec assurance. Vous restez responsable de ce que vous en faites.\u001fThe assistant can be confidently wrong. You stay responsible for what you do with it.": "El asistente puede estar seguro y estar equivocado. Tú sigues siendo responsable de lo que haces con él.", "L’assistant retient des informations utiles d’une conversation à l’autre.\u001fThe assistant keeps useful details across chats.": "El asistente conserva detalles útiles entre chats.", "L’assistant utilisé. Ses outils et réglages s’appliquent.\u001fThe assistant used. Its tools and settings apply.": "El asistente usado. Sus herramientas y configuraciones se aplican.", "L’assistant écrit et exécute du code pour obtenir un résultat exact.\u001fThe assistant writes and runs code to get an exact result.": "El asistente escribe y ejecuta código para obtener un resultado exacto.", "L’historique. La flèche replie la liste ; le menu ⋯ propose d’archiver ou supprimer. Clic droit sur une conversation pour l’épingler, la renommer ou la déplacer.\u001fYour history. The arrow collapses the list; ⋯ offers archive or delete. Right-click a chat to pin, rename or move it.": "Tu historial. La flecha contrae la lista; ⋯ permite archivar o borrar. Haz clic derecho en un chat para fijarlo, renombrarlo o moverlo.", "L’icône réglages permet d’ajuster ses options personnelles.\u001fThe settings icon adjusts its personal options.": "El icono de configuración ajusta sus opciones personales.", "L’écran d’accueil et les suggestions\u001fHome screen and suggestions": "Pantalla de inicio y sugerencias", "L’éditeur de note\u001fThe note editor": "El editor de notas", "Masquer la barre\u001fHide sidebar": "Ocultar barra lateral", "Mauvaise réponse\u001fBad response": "Mala respuesta", "Menu utilisateur\u001fUser menu": "Menú de usuario", "Menu utilisateur › Automations. Une automatisation envoie une demande à heure fixe : chaque exécution crée une conversation avec la réponse.\u001fUser menu › Automations. An automation sends a request on a schedule: each run creates a chat with the answer.": "Menú de usuario › Automatizaciones. Una automatización envía una solicitud programada: cada ejecución crea un chat con la respuesta.", "Menu utilisateur › Calendar.\u001fUser menu › Calendar.": "Menú de usuario › Calendario.", "Menu utilisateur › Calendar. Un agenda personnel avec rappels, que l’assistant peut aussi gérer pour vous.\u001fUser menu › Calendar. A personal calendar with reminders that the assistant can also manage for you.": "Menú de usuario › Calendario. Un calendario personal con recordatorios que el asistente también puede gestionar para ti.", "Messages épinglés\u001fPinned messages": "Mensajes fijados", "Met en pause ou relance sans supprimer.\u001fPauses or resumes without deleting.": "Pausar o reanudar sin borrar.", "Micro : parlez, relisez le texte, envoyez.\u001fMic: speak, review the text, send.": "Micrófono: hablar, revisar el texto, enviar.", "Mini calendrier\u001fMini calendar": "Calendario pequeño", "Modifier\u001fEdit": "Editar", "Modifiez les permissions sur un groupe de test avant de les appliquer à tous.\u001fChange permissions on a test group before applying them to everyone.": "Cambiar permisos en un grupo de prueba antes de aplicarlos a todos.", "Modèle généraliste\u001fGeneral model": "Modelo general", "Modèle raisonnement\u001fReasoning model": "Modelo de razonamiento", "Modèles de demande\u001fSaved prompts": "Prompts guardados", "Mots de passe, codes, identifiants\u001fPasswords, codes, credentials": "Contraseñas, códigos, credenciales", "Mémoire\u001fMemory": "Memoria", "Naviguer dans vos espaces\u001fMoving between spaces": "Movimiento entre espacios", "Ne collez jamais de mot de passe dans la conversation.\u001fNever paste a password into the chat.": "Nunca pegar una contraseña en el chat.", "Ne joignez que des documents que vous avez le droit d’utiliser.\u001fOnly attach documents you are allowed to use.": "Adjuntar solo los documentos que tienes permiso de usar.", "New Automation. La flèche propose Import JSON et Export JSON pour copier vos automatisations.\u001fNew Automation. The arrow offers Import JSON and Export JSON to copy your automations.": "Nueva Automatización. La flecha ofrece Importar JSON y Exportar JSON para copiar tus automatizaciones.", "New Event : titre, date, lieu, répétition, rappel.\u001fNew Event: title, date, location, repeat, reminder.": "Nuevo Evento: título, fecha, lugar, repetición, recordatorio.", "New Event ouvre ce formulaire, prérempli à la date du jour.\u001fNew Event opens this form, prefilled with today’s date.": "Nuevo Evento abre este formulario, pref llenado con la fecha de hoy.", "No Repeat, Daily, Monday – Friday, Weekly, Monthly ou Yearly. Pas plus souvent qu’une fois par jour : pour cela, utilisez une automatisation.\u001fNo Repeat, Daily, Monday – Friday, Weekly, Monthly or Yearly. No more than once a day: use an automation for that.": "Sin repetición, Diario, Lunes–Viernes, Semanal, Mensual o Anual. No más de una vez al día: usa una automatización para eso.", "Nom, fréquence et prochaine exécution. Cliquez pour ouvrir l’éditeur.\u001fName, schedule and next run. Click to open the editor.": "Nombre, programación y próxima ejecución. Haz clic para abrir el editor.", "Nombre de notes accessibles.\u001fNumber of notes you can access.": "Número de notas a las que puedes acceder.", "Not factually correct : erreur de fait. Didn’t fully follow instructions : consigne ignorée. Refused when it shouldn’t have : refus injustifié. Too verbose : trop long.\u001fNot factually correct: wrong facts. Didn’t fully follow instructions: ignored a request. Refused when it shouldn’t have: unjustified refusal. Too verbose: too long.": "No es factualmente correcto: hechos erróneos. No siguió completamente las instrucciones: ignoró una solicitud. Rechazó cuando no debería haberlo hecho: rechazo injustificado. Demasiado verboso: demasiado largo.", "Note de 1 à 5\u001fScore 1 to 5": "Puntuación 1 a 5", "Note de 6 à 10\u001fScore 6 to 10": "Puntuación 6 a 10", "Noter les bonnes et mauvaises réponses\u001fRating good and bad answers": "Valoración de buenas y malas respuestas", "Notes de réunion\u001fMeeting notes": "Notas de reunión", "Notes › Create.\u001fNotes › Create.": "Notas › Crear.", "Notez les réponses pour aider à améliorer les assistants.\u001fRate answers to help improve assistants.": "Valorar las respuestas para ayudar a mejorar los asistentes.", "Nouvelle conversation dans un dossier\u001fNew chat in a folder": "Nuevo chat en una carpeta", "N’activez que ce dont vous avez besoin : trop d’outils actifs ralentit et disperse les réponses.\u001fOnly turn on what you need: too many active tools slows and scatters answers.": "Activa solo lo que necesitas: demasiadas herramientas activas ralentizan y dispersan las respuestas.", "N’y enregistrez aucune donnée sensible.\u001fDo not store sensitive data there.": "No guardar datos sensibles allí.", "Obligatoire. Un nom clair.\u001fRequired. A clear name.": "Obligatorio. Un nombre claro.", "Once, Hourly, Daily, Weekly, Monthly ou Custom (règle avancée).\u001fOnce, Hourly, Daily, Weekly, Monthly or Custom (advanced rule).": "Una vez, por hora, diario, semanal, mensual o a medida (regla avanzada).", "Optionnel : salle ou lien de visio.\u001fOptional: room or video link.": "Opcional: enlace de sala o video.", "Optionnel : une image pour reconnaître le dossier.\u001fOptional: an image to recognize the folder.": "Opcional: una imagen para reconocer la carpeta.", "Ordre du jour, liens, documents à préparer.\u001fAgenda, links, documents to prepare.": "Agenda, enlaces, documentos a preparar.", "Ordre du jour…\u001fAgenda…": "Agenda…", "Organiser et collaborer\u001fOrganize and collaborate": "Organizar y colaborar", "Organiser vos projets\u001fOrganizing your projects": "Organizar tus proyectos", "Ou demandez à l’assistant : « ajoute le partiel de stats jeudi 14h ».\u001fOr ask the assistant: “add the stats exam on Thursday at 2pm”.": "O preguntar al asistente: «añadir el examen de estadística el jueves a las 2pm».", "Outil 1\u001fTool 1": "Herramienta 1", "Outil 2\u001fTool 2": "Herramienta 2", "Outil utilisé\u001fTool used": "Herramienta usada", "Outils de votre compte\u001fTools on your account": "Herramientas en tu cuenta", "Outils intégrés\u001fBuilt-in tools": "Herramientas integradas", "Outils, recherche web, fonctions\u001fTools, web search, features": "Herramientas, búsqueda web, funciones", "Ouvre le formulaire d’avis positif. Voir chapitre Avis.\u001fOpens the positive rating form. See the Feedback chapter.": "Abre el formulario de valoración positiva. Ver el capítulo Feedback.", "Ouvre le formulaire pour signaler un problème.\u001fOpens the form to report a problem.": "Abre el formulario para reportar un problema.", "Ouvre ou ferme le panneau assistant de la note.\u001fOpens or closes the note’s assistant panel.": "Abre o cierra el panel de asistente de la nota.", "Ouvre vos Notes.\u001fOpens your Notes.": "Abre tus Notas.", "Ouvrez Playground.\u001fOpen Playground.": "Abrir Playground.", "Ouvrez Settings › Personalization › Memory.\u001fOpen Settings › Personalization › Memory.": "Abrir Configuraciones › Personalización › Memoria.", "Ouvrez le dossier puis New Chat : la conversation hérite de ses réglages.\u001fOpen the folder then New Chat: the chat inherits its settings.": "Abrir la carpeta luego Nuevo Chat: el chat hereda sus configuraciones.", "Ouvrez le panneau Chat et utilisez une suggestion.\u001fOpen the Chat panel and use a suggestion.": "Abrir el panel de Chat y usar una sugerencia.", "Ouvrez les sources pour vérifier.\u001fOpen the sources to check.": "Abrir las fuentes para comprobarlo.", "Ouvrez un canal.\u001fOpen a channel.": "Abrir un canal.", "Ouvrir le chat\u001fOpen chat": "Abrir chat", "Ouvrir les sources affichées\u001fOpen the cited sources": "Abrir las fuentes citadas", "Où la trouver\u001fWhere to find it": "Dónde encontrarlo", "PDF, Word, Excel, images\u001fPDF, Word, Excel, images": "PDF, Word, Excel, imágenes", "Paramètres\u001fSettings": "Configuración", "Parcourt et lit les bases de documents accessibles.\u001fBrowses and reads accessible document collections.": "Navega y lee colecciones de documentos accesibles.", "Parlez : votre voix est transcrite en texte, que vous relisez avant d’envoyer.\u001fSpeak: your voice becomes text you can review before sending.": "Hablar: tu voz se convierte en texto que puedes revisar antes de enviar.", "Permissions des outils\u001fTool permissions": "Permisos de herramientas", "Plan de projet\u001fProject plan": "Plan de proyecto", "Plan du projet\u001fProject outline": "Esquema del proyecto", "Plus le contexte est précis, meilleure est la réponse. N’ajoutez que ce qui est utile à la question.\u001fPrecise context gives better answers. Only add what the question needs.": "Un contexto preciso da mejores respuestas. Añadir solo lo que la pregunta necesita.", "Plusieurs réponses côte à côte\u001fSeveral answers side by side": "Varias respuestas lado a lado", "Plusieurs réponses identiques peuvent partager la même erreur.\u001fMatching answers can share the same mistake.": "Respuestas similares pueden compartir el mismo error.", "Point équipe 9:00\u001fTeam standup 9:00": "Standup del equipo 9:00", "Posez la même question à plusieurs modèles et comparez.\u001fAsk several models the same question and compare.": "Pregunta a varios modelos la misma pregunta y compáralas.", "Posez une question précise.\u001fAsk a precise question.": "Haz una pregunta precisa.", "Posez votre propre demande sur la note.\u001fAsk your own request about the note.": "Haz tu propia solicitud sobre la nota.", "Posez votre question sur la page.\u001fAsk about the page.": "Pregunta sobre la página.", "Pour les actions qui modifient quelque chose, réglez + › Tool Permissions sur Ask for approval.\u001fFor actions that change something, set + › Tool Permissions to Ask for approval.": "Para acciones que cambian algo, configurar + › Permisos de herramienta en Pedir aprobación.", "Pour une demande en plusieurs étapes, l’assistant tient une liste de tâches et la coche au fur et à mesure.\u001fFor multi-step requests, the assistant keeps a task list and ticks items off.": "Para solicitudes de varios pasos, el asistente mantiene una lista de tareas y marca los ítems completados.", "Pour utiliser une note dans une conversation : + › Attach Notes.\u001fTo use a note in a chat: + › Attach Notes.": "Para usar una nota en un chat: + › Adjuntar Notas.", "Pourquoi utiliser des dossiers\u001fWhy use folders": "Por qué usar carpetas", "Prendre une capture d’écran d’une fenêtre ou de l’écran et la joindre au message.\u001fTake a screenshot of a window or your screen and attach it.": "Capturar una captura de pantalla de una ventana o de tu pantalla y adjuntarla.", "Produit une nouvelle version. Vous pouvez naviguer entre les versions.\u001fProduces a new version. You can switch between versions.": "Genera una nueva versión. Puedes alternar entre versiones.", "Programme un résumé des actualités du secteur chaque lundi à 8h.\u001fSchedule an industry news digest every Monday at 8am.": "Programar un resumen de noticias sectoriales todos los lunes a las 8am.", "Projets avec consignes et documents\u001fProjects with instructions and documents": "Proyectos con instrucciones y documentos", "Préciser votre demande si besoin\u001fRefine your request when needed": "Refinar tu solicitud cuando sea necesario", "Précisez la période, le pays et le type de source souhaité.\u001fSpecify the period, country and type of source you want.": "Especificar el período, país y tipo de fuente que deseas.", "Précisez période et sources souhaitées.\u001fSpecify the period and sources you want.": "Especificar el período y las fuentes que deseas.", "Précédent\u001fBack": "Atrás", "Préférences retenues\u001fRemembered preferences": "Preferencias recordadas", "Présentation du guide\u001fAbout this guide": "Acerca de este guía", "Quand la zone est vide, ce bouton lance le mode vocal. Dès que vous écrivez, il devient le bouton d’envoi.\u001fWhen the box is empty, this button starts voice mode. Once you type, it becomes the send button.": "Cuando el cuadro está vacío, este botón inicia modo voz. Una vez escribas, se convierte en el botón de enviar.", "Quelles sont les nouveautés officielles sur ce sujet en 2026 ? Donne les liens.\u001fWhat was officially published on this topic in 2026? Give the links.": "¿Qué se publicó oficialmente sobre este tema en 2026? Da los enlaces.", "Quelles sont les règles en vigueur en 2026 ? Cite la source officielle.\u001fWhat rules apply in 2026? Cite the official source.": "¿Qué normas aplican en 2026? Cita la fuente oficial.", "Qui peut voir ou modifier la note. Par défaut elle est privée.\u001fWho can view or edit the note. Private by default.": "Quién puede ver o editar la nota. Privada por defecto.", "Qu’est-ce que j’ai dans mon calendrier cette semaine ?\u001fWhat is on my calendar this week?": "¿Qué hay en mi calendario esta semana?", "Range automatiquement les conversations produites dans un de vos dossiers.\u001fFiles the chats it creates into one of your folders.": "Archiva los chats que crea en una de tus carpetas.", "Rangez les conversations d’un projet et partagez consignes et documents entre elles.\u001fGroup a project’s chats and share instructions and documents between them.": "Agrupar los chats de un proyecto y compartir instrucciones y documentos entre ellos.", "Rappel quotidien\u001fDaily reminder": "Recordatorio diario", "Rappelle-moi vendredi à 15h de relancer le prestataire.\u001fRemind me on Friday at 3pm to follow up with the supplier.": "Recordarme el viernes a las 3pm para seguir con el proveedor.", "Recherche dans les titres et le contenu.\u001fSearches titles and content.": "Busca títulos y contenido.", "Recherche les actualités du secteur de la semaine et résume-les en 5 points avec les liens.\u001fSearch this week’s industry news and summarize it in 5 points with links.": "Buscar las noticias sectoriales de esta semana y resumirlas en 5 puntos con enlaces.", "Recherche sur Internet et cite les pages consultées.\u001fSearches the internet and cites the pages used.": "Busca en internet y cita las páginas usadas.", "Recherche web\u001fWeb search": "Búsqueda web", "Recherche, lit, crée et met à jour vos notes.\u001fSearches, reads, creates and updates your notes.": "Busca, lee, crea y actualiza tus notas.", "Rechercher\u001fSearch": "Buscar", "Rechercher des conversations\u001fSearch chats": "Buscar chats", "Rechercher des fichiers\u001fSearch files": "Buscar archivos", "Rechercher des notes\u001fSearch notes": "Buscar notas", "Regroupez vos conversations par projet. Le + crée un dossier.\u001fGroup chats by project. The + creates a folder.": "Agrupar chats por proyecto. El + crea una carpeta.", "Relisez les résultats : l’automatisation s’exécute sans vous.\u001fReview results: the automation runs without you.": "Revisar resultados: la automatización se ejecuta sin ti.", "Remplissez les champs puis Create. L’éditeur permet ensuite de lancer, suspendre et suivre les exécutions.\u001fFill in the fields then Create. The editor then lets you run, pause and monitor executions.": "Rellena los campos luego Crea. El editor luego te permite ejecutar, pausar y supervisar las ejecuciones.", "Replie la barre pour gagner de la place. Cliquez à nouveau pour la rouvrir.\u001fCollapses the sidebar for more room. Click again to reopen.": "Contrae la barra lateral para más espacio. Haz clic otra vez para volver a abrir.", "Reprenez quand vous voulez\u001fResume anytime": "Reanudar cuando quieras", "Repérez la ligne « outil utilisé » et ouvrez-la pour vérifier.\u001fLook for the “tool used” line and open it to check.": "Buscar la línea «herramienta usada» y abrirla para comprobarlo.", "Respectez les règles de votre organisation sur l’usage de l’IA.\u001fFollow your organization’s rules on using AI.": "Sigue las reglas de tu organización sobre el uso de la IA.", "Retient ou retrouve une préférence : « souviens-toi que je travaille sur le projet Alpha ».\u001fSaves or recalls a preference: “remember I work on Project Alpha”.": "Guarda o recuerda una preferencia: «recuerda que trabajo en Proyecto Alpha».", "Retour\u001fBack": "Atrás", "Retrouve dans mes notes ce que j’ai écrit sur le projet Alpha.\u001fFind what I wrote about Project Alpha in my notes.": "Buscar lo que escribí sobre Proyecto Alpha en mis notas.", "Retrouve une ancienne conversation : « qu’avions-nous décidé sur le budget ? ».\u001fFinds a past chat: “what did we decide about the budget?”.": "Encuentra un chat anterior: «¿qué decidimos sobre el presupuesto?».", "Retrouve une conversation par mot-clé.\u001fFinds a chat by keyword.": "Busca un chat por palabra clave.", "Retrouver une automatisation par son nom.\u001fFind an automation by name.": "Buscar una automatización por nombre.", "Revenez sur une modification, y compris celles faites par l’assistant.\u001fUndo any change, including those made by the assistant.": "Deshacer cualquier cambio, incluyendo los realizados por el asistente.", "Revient à aujourd’hui. Les flèches passent à la période précédente ou suivante.\u001fReturns to today. Arrows move to the previous or next period.": "Vuelve a hoy. Las flechas mueven al período anterior o siguiente.", "Rouvrir le guide\u001fReopen guide": "Volver a abrir guía", "Règlement intérieur\u001fInternal rules": "Reglas internas", "Règles d’utilisation\u001fAcceptable use": "Uso aceptable", "Règles essentielles\u001fEssential rules": "Reglas esenciales", "Réactions\u001fReactions": "Reacciones", "Rédiger avec l’assistant\u001fWrite with the assistant": "Escribe con el asistente", "Rédiger avec l’assistant\u001fWriting with the assistant": "Escribir con el asistente", "Rédiger un e-mail\u001fDraft an email": "Borrador de un correo electrónico", "Rédigez à gauche, travaillez avec l’assistant à droite.\u001fWrite on the left, work with the assistant on the right.": "Escribe a la izquierda, trabaja con el asistente a la derecha.", "Réglez + › Tool Permissions sur Ask for approval pour valider chaque action.\u001fSet + › Tool Permissions to Ask for approval to confirm each action.": "Establecer + › Permisos de herramientas en Pedir aprobación para confirmar cada acción.", "Réglez l’interface selon vos préférences.\u001fAdjust the interface to your preferences.": "Ajusta la interfaz a tus preferencias.", "Régénérer\u001fRegenerate": "Regenerar", "Répondez en fil pour séparer les sujets.\u001fReply in threads to separate topics.": "Responder en hilos para separar los temas.", "Répondez à un message précis pour garder les sujets séparés.\u001fReply to a specific message to keep topics separate.": "Responder a un mensaje específico para mantener los temas separados.", "Réponds uniquement à partir de ces documents et indique la section utilisée.\u001fAnswer only from these documents and name the section used.": "Responde solo de estos documentos y nombra la sección utilizada.", "Réponses\u001fAnswers": "Respuestas", "Réservé aux administrateurs\u001fAdmins only": "Solo administradores", "Réservé aux administrateurs : tester un modèle avec ses paramètres bruts, hors conversation.\u001fAdmins only: test a model with raw parameters, outside a chat.": "Solo administradores: probar un modelo con parámetros sin procesar, fuera de un chat.", "Réservé aux administrateurs : utilisateurs, groupes et permissions, réglages, évaluations.\u001fAdmins only: users, groups and permissions, settings, evaluations.": "Solo administradores: usuarios, grupos y permisos, configuración, evaluaciones.", "Résume cette page en 5 points pour quelqu’un qui découvre le sujet.\u001fSummarize this page in 5 points for someone new to the topic.": "Resumir esta página en 5 puntos para alguien nuevo en el tema.", "Résumer un document\u001fSummarize a document": "Resumir un documento", "Résumé hebdo 8:00\u001fWeekly digest 8:00": "Resumen semanal 8:00", "Résumé hebdomadaire des actualités\u001fWeekly news digest": "Resumen semanal de noticias", "Réunion de projet\u001fProject meeting": "Reunión de proyecto", "Réunion projet 10:00\u001fProject meeting 10:00": "Reunión de proyecto 10:00", "Réutiliser une conversation\u001fReuse a conversation": "Reutilizar una conversación", "Salle 204\u001fRoom 204": "Sala 204", "Sans recherche web, l’assistant répond avec des connaissances qui peuvent être anciennes.\u001fWithout web search, the assistant answers from knowledge that may be outdated.": "Sin búsqueda web, el asistente responde desde conocimiento que puede estar desactualizado.", "Select Knowledge ajoute une base existante, Upload envoie vos fichiers. Ils servent de contexte à chaque conversation du dossier.\u001fSelect Knowledge adds an existing collection, Upload sends your files. They become context for every chat in the folder.": "Seleccionar Conocimiento añade una colección existente, Subir envía tus archivos. Se convierten en contexto para cada chat de la carpeta.", "Selon sa configuration, l’assistant peut agir directement : consulter la date, chercher dans vos notes, créer un événement… Vous le demandez en langage naturel, il choisit l’outil.\u001fDepending on its setup, the assistant can act directly: check the date, search your notes, create an event… You ask in plain language and it picks the tool.": "Dependiendo de su configuración, el asistente puede actuar directamente: comprobar la fecha, buscar en tus notas, crear un evento… Tú pides en lenguaje plano y élige la herramienta.", "Settings › General pour la langue et le thème.\u001fSettings › General for language and theme.": "Configuración › General para idioma y tema.", "Si la réponse s’arrête en cours de route, l’assistant reprend là où il s’était arrêté.\u001fIf the answer stops midway, the assistant picks up where it left off.": "Si la respuesta se corta a mitad, el asistente retoma donde lo dejó.", "Signaler une mauvaise réponse\u001fReporting a bad answer": "Reportar una mala respuesta", "Sommaire\u001fContents": "Contenidos", "Sous la zone de saisie d’une nouvelle conversation, des suggestions montrent des usages typiques de l’assistant.\u001fBelow the message box of a new chat, suggestions show typical uses of the assistant.": "Debajo de la caja de mensajes de un nuevo chat, las sugerencias muestran usos típicos del asistente.", "Sous une réponse\u001fUnder an answer": "Bajo una respuesta", "Sous une réponse : haut-parleur pour l’écouter.\u001fUnder an answer: speaker to listen.": "Bajo una respuesta: orador a escuchar.", "Statut visible par les autres\u001fStatus shown to others": "Estado visible para otros", "Suivant\u001fNext": "Siguiente", "Support\u001fSupport": "Soporte", "Supprime définitivement la note.\u001fPermanently deletes the note.": "Borra permanentemente la nota.", "Supprime l’automatisation et son historique.\u001fDeletes the automation and its history.": "Elimina la automatización y su historial.", "Survolez une réponse : cette barre apparaît. Chaque icône a un rôle précis.\u001fHover an answer: this bar appears. Each icon has a specific role.": "Pasar el cursor sobre una respuesta: aparece esta barra. Cada icono tiene un rol específico.", "Survolez une réponse.\u001fHover an answer.": "Pasar el cursor sobre una respuesta.", "Survolez-la pour lire le rôle de l’assistant avant de le choisir.\u001fHover it to read what the assistant is for before choosing it.": "Pasar el cursor para leer de qué se trata el asistente antes de elegirlo.", "Suspend sans perdre les réglages.\u001fSuspends without losing settings.": "Pausar sin perder la configuración.", "Sélecteur d’assistant\u001fAssistant selector": "Selector de asistente", "Tapez /\u001fType /": "Tipo /", "Tapez / au début du message.\u001fType / at the start of the message.": "Escribe / al inicio del mensaje.", "Tapez @ et choisissez un assistant.\u001fType @ and pick an assistant.": "Escribe @ y selecciona un asistente.", "Tapez @ puis choisissez un assistant : il répond dans un fil sous votre message, sans encombrer le canal.\u001fType @ then pick an assistant: it replies in a thread under your message, keeping the channel tidy.": "Escribe @ luego selecciona un asistente: responde en un hilo bajo tu mensaje, manteniendo ordenado el canal.", "Tapez un nom pour filtrer. « All » filtre par catégorie.\u001fType a name to filter. “All” filters by category.": "Escribe un nombre para filtrar. «Todos» filtra por categoría.", "Tester un modèle avec ses paramètres, hors conversation.\u001fTest a model with its parameters, outside a chat.": "Probar un modelo con sus parámetros, fuera de un chat.", "Tester un modèle en direct avec prompt système et paramètres, sans créer de conversation.\u001fTest a model live with system prompt and parameters, without creating a chat.": "Probar un modelo en vivo con prompt de sistema y parámetros, sin crear un chat.", "Testez avec Run now, suivez les Execution logs.\u001fTest with Run now, check Execution logs.": "Probar con Ejecutar ahora, revisar los registros de ejecución.", "Testez chaque changement sur un compte non administrateur.\u001fTest every change with a non-admin account.": "Probar cada cambio con una cuenta no administrativa.", "Testez toujours avec Run now. Vous pouvez aussi demander dans une conversation : « programme un résumé tous les jours à 9h ».\u001fAlways test with Run now. You can also ask in a chat: “schedule a summary every day at 9am”.": "Siempre prueba con Ejecutar ahora. También puedes preguntar en un chat: “programa un resumen todos los días a las 9 am”.", "Title, Instructions, Model, Schedule, Folder.\u001fTitle, Instructions, Model, Schedule, Folder.": "Título, Instrucciones, Modelo, Programación, Carpeta.", "Titre\u001fTitle": "Título", "Tous les membres du canal lisent ce que vous publiez. Vérifiez qui y a accès avant de partager un document.\u001fEvery channel member reads what you post. Check who has access before sharing a document.": "Cada miembro del canal lee lo que publicas. Comprobar quién tiene acceso antes de compartir un documento.", "Tous les membres voient vos messages.\u001fAll members see your messages.": "Todos los miembros ven tus mensajes.", "Tout part de cette zone. Voici à quoi sert chaque bouton.\u001fEverything starts here. Here is what each button does.": "Todo empieza aquí. Aquí está lo que hace cada botón.", "Toutes les actions sur la note.\u001fAll actions for the note.": "Todas las acciones para la nota.", "Toutes les fonctions disponibles pour vous\u001fEvery feature available to you": "Cada función disponible para ti", "Toutes, actives ou en pause.\u001fAll, active or paused.": "Todas, activas o pausadas.", "Transforme cette note en compte rendu : décisions, responsables, échéances.\u001fTurn this note into minutes: decisions, owners, deadlines.": "Convertir esta nota en actas: decisiones, responsables, fechas límite.", "Travailler à plusieurs\u001fWorking together": "Trabajando juntos", "Trouve les informations officielles les plus récentes sur ce sujet et donne le lien de chaque source.\u001fFind the latest official information on this topic and give the link to each source.": "Buscar la información oficial más reciente sobre este tema e incluir el enlace a cada fuente.", "Tu es mon tuteur en statistiques. Explique avec des exemples concrets, vérifie mes calculs et pose-moi une question pour valider ma compréhension.\u001fYou are my statistics tutor. Explain with concrete examples, check my calculations and ask me a question to confirm I understood.": "Eres mi tutor de estadísticas. Explica con ejemplos concretos, comprueba mis cálculos y pregúntame una pregunta para confirmar que he entendido.", "Tutoriels\u001fTutorials": "Tutoriales", "Télécharger, partager, épingler, supprimer. Voir étape suivante.\u001fDownload, share, pin, delete. See next step.": "Descargar, compartir, fijar, eliminar. Ver siguiente paso.", "Un assistant spécialisé répond à partir de ses documents. Pour résumer vos fichiers ou chercher sur Internet, prenez un modèle généraliste.\u001fA specialized assistant answers from its own documents. To summarize your files or search the web, pick a general model.": "Un asistente especializado responde desde sus propios documentos. Para resumir tus archivos o buscar en la web, elige un modelo general.", "Un canal est un espace partagé en temps réel. L’IA n’intervient que si vous la mentionnez avec @.\u001fA channel is a real-time shared space. AI only joins in when you mention it with @.": "Un canal es un espacio compartido en tiempo real. La IA solo participa cuando la mencionas con @.", "Un dossier est un projet : il range vos conversations ET donne à l’assistant les mêmes consignes et documents pour chaque conversation qu’il contient.\u001fA folder is a project: it groups your chats AND gives the assistant the same instructions and documents for every chat inside.": "Una carpeta es un proyecto: agrupa tus chats Y da al asistente las mismas instrucciones y documentos para cada chat dentro.", "Un emoji et un court message pour indiquer votre disponibilité.\u001fAn emoji and a short message to show your availability.": "Un emoji y un mensaje corto para mostrar tu disponibilidad.", "Un emoji pour approuver sans ajouter de message.\u001fAn emoji to agree without adding a message.": "Un emoji para mostrar acuerdo sin añadir otro mensaje.", "Un nom clair, sans donnée sensible.\u001fA clear name, no sensitive data.": "Un nombre claro, sin datos confidenciales.", "Un nom qui dit ce que produit l’automatisation.\u001fA name saying what it produces.": "Un nombre claro, sin datos confidenciales.", "Un événement\u001fAn event": "Un evento", "Une automatisation\u001fAn automation": "Una automatización", "Une note\u001fA note": "Una nota", "Une phrase suffit pour dire ce qui vous a aidé.\u001fOne sentence is enough to say what helped.": "Una frase es suficiente para indicar qué ayudó.", "Update your status, choisissez un emoji et un texte.\u001fUpdate your status, pick an emoji and text.": "Actualiza tu estado, elige un emoji y texto.", "Utilisateurs, groupes et permissions (qui voit Notes, Calendar, Automations…), réglages généraux, évaluations des modèles à partir des avis.\u001fUsers, groups and permissions (who sees Notes, Calendar, Automations…), general settings, model evaluations from feedback.": "Usuarios, grupos y permisos (quién ve Notas, Calendar, Automatizaciones…), configuraciones generales, evaluaciones de modelos desde feedback.", "Utilisateurs, groupes, permissions, réglages et évaluations.\u001fUsers, groups, permissions, settings and evaluations.": "Usuarios, grupos, permisos, configuraciones y evaluaciones.", "Utiliser cet assistant par défaut pour vos nouvelles conversations.\u001fUse this assistant by default for new chats.": "Usar este asistente por defecto para nuevos chats.", "Utiliser l’IA de façon responsable\u001fUsing AI responsibly": "Uso de IA responsable", "Utiliser un outil\u001fUsing a tool": "Usando una herramienta", "Utilisez Ask for approval pour valider les actions.\u001fUse Ask for approval to confirm actions.": "Usar «Pedir aprobación» para confirmar acciones.", "V\u001fY": "Y", "Veille\u001fWatch": "Ver", "Veille concurrentielle\u001fMarket watch": "Monitor de mercado", "Visite guidée\u001fGuided tour": "Recorrido guiado", "Voir toutes les fonctions\u001fSee all features": "Ver todas las funciones", "Voix\u001fVoice": "Voz", "Vos assistants épinglés, pour les ouvrir en un clic.\u001fYour pinned assistants, one click away.": "Tus asistentes fijados, a un clic de distancia.", "Vos automatisations sont privées : personne d’autre ne peut les voir ni les lancer.\u001fYour automations are private: nobody else can see or run them.": "Tus automatizaciones son privadas: nadie más puede verlas o ejecutarlas.", "Vos documents personnels, avec un assistant intégré. Voir chapitre Notes.\u001fYour personal documents with a built-in assistant. See the Notes chapter.": "Tus documentos personales con un asistente integrado. Ver el capítulo Notas.", "Votre agenda personnel, avec rappels. Voir chapitre Calendrier.\u001fYour personal calendar with reminders. See the Calendar chapter.": "Tu calendario personal con recordatorios. Ver el capítulo Calendario.", "Votre compte\u001fYour account": "Tu cuenta", "Votre nom\u001fYour name": "Tu nombre", "Votre nom (en bas à gauche)\u001fYour name (bottom left)": "Tu nombre (esquina inferior izquierda)", "Votre nom en bas à gauche\u001fYour name, bottom left": "Tu nombre, esquina inferior izquierda", "Votre question\u001fYour question": "Tu pregunta", "Vous\u001fYou": "Tú", "Vous voyez ce chapitre car votre compte est administrateur.\u001fYou see this chapter because your account is an administrator.": "Ves este capítulo porque tu cuenta es de administrador.", "Vous êtes prêt\u001fYou’re ready": "Estás listo", "Vérifier chiffres, dates et citations\u001fCheck figures, dates and quotes": "Comprobar cifras, fechas y citas", "Zone de saisie\u001fMessage box": "Caja de mensajes", "annonces\u001fannouncements": "anuncios", "chapitres\u001fchapters": "capítulos", "icône d’action\u001faction icon": "icono de acción", "projet-alpha\u001fproject-alpha": "proyecto-alpha", "« La date limite indiquée est le 15 mars, mais le site officiel indique le 31 mars. »\u001f“It says the deadline is 15 March, but the official site says 31 March.”": "«Dice que la fecha límite es el 15 de marzo, pero el sitio oficial dice 31 de marzo».", "« Nul. »\u001f“Bad.”": "“Mal.”", "· Guide et tutoriels\u001f· Guide & tutorials": "· Guía & tutoriales", "À faire\u001fDo": "Hacer", "À gauche, tout pour naviguer entre vos conversations et espaces.\u001fOn the left, everything to move between chats and spaces.": "A la izquierda, todo para moverse entre chats y espacios.", "À partir de la conversation jointe, rédige la version finale de l’e-mail.\u001fUsing the attached chat, write the final version of the email.": "Usando el chat adjunto, escribe la versión final del correo.", "À éviter\u001fAvoid": "Evitar", "Écoutez la réponse.\u001fListen to the answer.": "Escuchar la respuesta.", "Écrire une bonne demande, ajouter des documents, activer les outils, organiser vos projets, noter les réponses et utiliser l’IA de façon responsable.\u001fWriting good requests, adding documents, turning on tools, organizing projects, rating answers and using AI responsibly.": "Redactar buenas solicitudes, añadir documentos, activar herramientas, organizar proyectos, calificar respuestas y usar IA responsablemente.", "Écrivez librement. Sélectionnez un passage pour le faire réécrire par l’assistant.\u001fWrite freely. Select a passage to have the assistant rewrite it.": "Escribe libremente. Selecciona un pasaje para que el asistente lo reescriba.", "Écrivez votre demande ici. Entrée pour envoyer, Maj + Entrée pour aller à la ligne.\u001fType your request here. Enter sends, Shift + Enter adds a new line.": "Escribe tu solicitud aquí. Enter envía, Shift + Enter añade una nueva línea.", "Écrivez, joignez un fichier avec +, mentionnez une personne ou un assistant.\u001fWrite, attach a file with +, mention a person or an assistant.": "Escribe, adjunta un archivo con +, menciona a una persona o un asistente.", "Étape \u001fStep ": "Paso ", "Étiquettes\u001fTags": "Etiquetas", "Événements et rappels\u001fEvents and reminders": "Eventos y recordatorios", "Événements, rappels, IA\u001fEvents, reminders, AI": "Eventos, recordatorios, IA", "écran\u001fscreen": "pantalla", "écrans\u001fscreens": "pantallas", "⋯ › Pin to Sidebar pour la garder à portée.\u001f⋯ › Pin to Sidebar to keep it handy.": "⋎ › Fijar en barra lateral para tenerlo a mano.", "👍 / 👎 sous chaque réponse\u001f👍 / 👎 under each answer": "👍 / 👎 bajo cada respuesta", "👍 ouvre ce formulaire. Vos avis aident votre organisation à choisir et améliorer les assistants.\u001f👍 opens this form. Your ratings help your organization choose and improve assistants.": "👎 abre este formulario. Tus valoraciones ayudan a tu organización a elegir y mejorar los asistentes.", "👎 ouvre la version négative. Un avis précis est bien plus utile qu’une simple note.\u001f👎 opens the negative version. A precise rating is far more useful than a score alone.": "👎 abre la versión negativa. Una valoración precisa es mucho más útil que solo una puntuación."}}
ADMIN_LOCALE_KEYS = ('Admin Panel › Users › Groups pour les permissions.\x1fAdmin Panel › Users › Groups for permissions.', 'Administrateurs\x1fAdministrators', 'Administration\x1fAdministration', 'Choisissez un modèle, un prompt système et envoyez.\x1fPick a model, a system prompt and send.', 'Espace administrateur\x1fAdministrator area', 'Modifiez les permissions sur un groupe de test avant de les appliquer à tous.\x1fChange permissions on a test group before applying them to everyone.', 'Ouvrez Playground.\x1fOpen Playground.', 'Réservé aux administrateurs\x1fAdmins only', 'Réservé aux administrateurs : tester un modèle avec ses paramètres bruts, hors conversation.\x1fAdmins only: test a model with raw parameters, outside a chat.', 'Réservé aux administrateurs : utilisateurs, groupes et permissions, réglages, évaluations.\x1fAdmins only: users, groups and permissions, settings, evaluations.', 'Tester un modèle avec ses paramètres, hors conversation.\x1fTest a model with its parameters, outside a chat.', 'Tester un modèle en direct avec prompt système et paramètres, sans créer de conversation.\x1fTest a model live with system prompt and parameters, without creating a chat.', 'Testez chaque changement sur un compte non administrateur.\x1fTest every change with a non-admin account.', 'Utilisateurs, groupes et permissions (qui voit Notes, Calendar, Automations…), réglages généraux, évaluations des modèles à partir des avis.\x1fUsers, groups and permissions (who sees Notes, Calendar, Automations…), general settings, model evaluations from feedback.', 'Utilisateurs, groupes, permissions, réglages et évaluations.\x1fUsers, groups, permissions, settings and evaluations.', 'Vous voyez ce chapitre car votre compte est administrateur.\x1fYou see this chapter because your account is an administrator.')
# END GENERATED LOCALE DATA

FALLBACK_TEXT_FR = """## Bienvenue

Votre guide interactif et personnalisé s'affiche au-dessus de ce message. Il est
construit uniquement à partir des modèles, prompts, outils, compétences, bases de
connaissances et fonctions auxquels votre compte peut accéder.

Si le guide ne s'affiche pas, actualisez la page ou contactez votre équipe de support.
"""

FALLBACK_TEXT_EN = """## Welcome

Your personalized interactive guide appears above this message. It is built only
from models, prompts, tools, skills, knowledge bases, and functions your account
is allowed to access.

If the guide does not appear, refresh the page or contact your support team.
"""


# The iframe is intentionally self-contained. It loads no scripts, fonts, images,
# or data from third-party origins. Dynamic values are serialized as inert JSON.
ONBOARDING_HTML = r"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; form-action 'none';">
<title>Guide</title>
<style>
:root{
  color-scheme:light;
  --bleu:#1F4E79;--turq:#0EA5B7;--jaune:#F0B429;--orange:#E8710A;--nuit:#0B1F33;
  --primary:var(--bleu);--primary-hover:#082F50;--on-primary:#fff;--accent:var(--turq);
  --ring:rgba(0,179,195,.28);--bleu-tint:#EAF1F7;--turq-tint:#E3F7F9;--jaune-tint:#FFF6DB;--orange-tint:#FFF0E0;
  --bg:#fff;--stage:#F2F5F8;--surface:#fff;--subtle:#F3F4F6;--hover:#F5F5F5;
  --text:#111827;--muted:#6B7280;--faint:#9CA3AF;--border:#E5E7EB;--border-strong:#D1D5DB;
  --send:#000;--send-fg:#fff;--head:var(--bleu);--head-text:#fff;--head-muted:rgba(255,255,255,.72);
  --font:Arial,"Helvetica Neue",Helvetica,sans-serif
}
@media(prefers-color-scheme:dark){:root{
  color-scheme:dark;--primary:var(--turq);--primary-hover:#2FC2D4;--on-primary:var(--nuit);
  --bleu-tint:#12293D;--turq-tint:#07333A;--jaune-tint:#2B2409;--orange-tint:#2E1B08;
  --bg:#141414;--stage:#0C1B29;--surface:#1E1E1E;--subtle:#262626;--hover:#2A2A2A;
  --text:#ECECEC;--muted:#A3A3A3;--faint:#737373;--border:#2E2E2E;--border-strong:#404040;
  --send:#ECECEC;--send-fg:#111;--head:var(--nuit)
}}
*{box-sizing:border-box}
html,body{margin:0}
body{padding:2px;background:transparent;color:var(--text);font:14px/1.55 var(--font);-webkit-font-smoothing:antialiased}
button{font:inherit;color:inherit;background:none;border:0;padding:0;cursor:pointer;text-align:left}
button:focus-visible{outline:2px solid var(--turq);outline-offset:2px;border-radius:6px}
.i{width:16px;height:16px;flex:0 0 auto;fill:none;stroke:currentColor;stroke-width:1.75;stroke-linecap:round;stroke-linejoin:round}

/* ===== Guide chrome ===== */
.g{position:relative;max-width:900px;margin:0 auto;border:1px solid var(--border);border-radius:16px;background:var(--bg);overflow:hidden}
.g-head{display:flex;align-items:center;gap:14px;padding:12px 18px;background:var(--head);color:var(--head-text)}
.g-logo{display:grid;width:28px;height:28px;flex:0 0 auto;place-items:center;border-radius:50%;background:#fff;color:var(--bleu);font-weight:800;font-size:13px}
.g-name{font-size:14px;font-weight:650;white-space:nowrap}
.g-name span{font-weight:400;color:var(--head-muted)}
.g-tabs{display:flex;gap:4px;margin-left:8px}
.g-tab{padding:6px 10px;border-radius:8px;font-size:13px;font-weight:600;color:var(--head-muted);transition:all 160ms ease-out}
.g-tab:hover{color:#fff;background:rgba(255,255,255,.08)}
.g-tab[aria-selected="true"]{color:#fff;box-shadow:inset 0 -2px 0 var(--jaune);border-radius:8px 8px 0 0}
.g-right{display:flex;align-items:center;gap:6px;margin-left:auto}
.g-lang{display:flex;max-width:220px;padding:2px;overflow-x:auto;border-radius:8px;background:rgba(255,255,255,.12);scrollbar-width:none}
.g-lang::-webkit-scrollbar{display:none}
.g-lang button{padding:2px 8px;border-radius:6px;font-size:12px;font-weight:700;color:var(--head-muted)}
.g-lang button[aria-pressed="true"]{background:#fff;color:var(--bleu)}
.g-close{display:flex;align-items:center;gap:4px;padding:5px 8px;border-radius:8px;font-size:13px;color:var(--head-muted);transition:all 160ms ease-out}
.g-close:hover{color:#fff;background:rgba(255,255,255,.1)}

.g-chapters{display:flex;gap:6px;padding:12px 18px;overflow-x:auto;border-bottom:1px solid var(--border);scrollbar-width:none}
.g-chapters::-webkit-scrollbar{display:none}
.chip{flex:0 0 auto;padding:5px 11px;border:1px solid var(--border);border-radius:999px;font-size:12.5px;font-weight:600;color:var(--muted);white-space:nowrap;transition:all 160ms ease-out}
.chip:hover{color:var(--text);border-color:var(--border-strong)}
.chip.done{color:var(--bleu);border-color:transparent;background:var(--bleu-tint)}
.chip.on{color:#fff;border-color:var(--bleu);background:var(--bleu)}
@media(prefers-color-scheme:dark){.chip.done{color:var(--turq)}.chip.on{color:var(--nuit);background:var(--turq);border-color:var(--turq)}}
.g-bar{height:3px;background:var(--border)}
.g-bar span{display:block;height:100%;background:var(--turq);transition:width 220ms ease-out}

/* ===== Step ===== */
.step{padding:22px 18px 8px;animation:fade 200ms ease-out both}
.kicker{display:flex;align-items:center;gap:8px;margin:0 0 4px;font-size:12px;font-weight:700;letter-spacing:.02em;color:var(--primary)}
.kicker b{padding:1px 7px;border-radius:999px;background:var(--jaune);color:var(--nuit);font-size:11px}
.step h1{margin:0;font-size:22px;line-height:1.3;font-weight:700;letter-spacing:-.01em}
.desc{max-width:720px;margin:6px 0 0;color:var(--muted);font-size:14.5px}
.hint{display:inline-flex;align-items:center;gap:6px;margin-top:10px;font-size:12.5px;color:var(--muted)}
.hint .i{color:var(--turq)}

.stage{position:relative;display:flex;justify-content:center;align-items:flex-start;min-height:300px;margin-top:16px;padding:24px 20px;border:1px solid var(--border);border-radius:14px;background:var(--stage);overflow:hidden}
.stage.center{align-items:center;min-height:220px}
.stage.tall{min-height:470px}
.stage.flush{padding:14px}

.notes{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:14px}
.note{display:flex;gap:10px;padding:10px 12px;border:1px solid transparent;border-radius:12px;transition:all 160ms ease-out}
.note:hover{background:var(--hover)}
.note.on{border-color:var(--turq);background:var(--turq-tint)}
.note-n{display:grid;width:20px;height:20px;flex:0 0 auto;place-items:center;border-radius:50%;background:var(--bleu);color:#fff;font-size:11px;font-weight:700}
@media(prefers-color-scheme:dark){.note-n{background:var(--turq);color:var(--nuit)}}
.note-b{min-width:0}
.note-k{display:flex;align-items:center;gap:6px;font-size:13.5px;font-weight:650}
.note-k .i{width:15px;height:15px;color:var(--muted)}
.note-t{margin-top:2px;font-size:13px;color:var(--muted)}

.callout{display:flex;gap:10px;margin-top:12px;padding:11px 14px;border-radius:12px;font-size:13.5px}
.callout .i{margin-top:2px}
.callout.tip{background:var(--jaune-tint);border-left:3px solid var(--jaune)}
.callout.tip .i{color:#B38300}
.callout.warn{background:var(--orange-tint);border-left:3px solid var(--orange)}
.callout.warn .i{color:var(--orange)}
.callout.info{background:var(--bleu-tint);border-left:3px solid var(--bleu)}
.callout.info .i{color:var(--primary)}
.example{margin-top:12px;padding:12px 14px;border:1px solid var(--border);border-radius:12px}
.example small{display:block;margin-bottom:4px;font-size:11.5px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.example p{margin:0;font-size:13.5px}
.try{display:inline-flex;align-items:center;gap:6px;margin-top:8px;font-size:13px;font-weight:650;color:var(--primary)}
.try:hover{text-decoration:underline}
.compare{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px}
.compare div{padding:12px 14px;border-radius:12px;font-size:13.5px}
.compare .bad{background:var(--orange-tint)}
.compare .good{background:var(--turq-tint)}
.compare b{display:flex;align-items:center;gap:6px;margin-bottom:4px;font-size:12px}

.g-foot{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:16px;padding:12px 18px;border-top:1px solid var(--border)}
.g-count{font-size:12.5px;color:var(--muted)}
.btn{display:inline-flex;align-items:center;gap:6px;min-height:36px;padding:0 14px;border-radius:10px;font-size:14px;font-weight:650;transition:all 160ms ease-out}
.btn.ghost{color:var(--muted)}
.btn.ghost:hover{color:var(--text);background:var(--hover)}
.btn.ghost:disabled{visibility:hidden}
.btn.primary{background:var(--primary);color:var(--on-primary)}
.btn.primary:hover{background:var(--primary-hover)}

/* ===== Library ===== */
.lib{position:relative;padding:22px 18px 18px;min-height:420px}
.lib-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin-top:16px}
.tile{display:flex;flex-direction:column;gap:4px;padding:14px;border:1px solid var(--border);border-radius:12px;transition:all 160ms ease-out}
.tile:hover{border-color:var(--turq);background:var(--turq-tint)}
.tile-k{display:flex;align-items:center;gap:8px;font-size:14px;font-weight:650}
.tile-k .i{width:18px;height:18px;color:var(--primary)}
.tile-t{font-size:12.5px;color:var(--muted)}
.tile-w{margin-top:4px;font-size:11.5px;color:var(--faint)}
.lib-group{margin:18px 0 0;font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.overlay{position:absolute;inset:0;display:flex;justify-content:center;align-items:flex-start;padding:18px;background:rgba(6,30,51,.45);animation:fadeonly 160ms ease-out both;z-index:20}
.sheet{width:100%;max-width:560px;border-radius:16px;background:var(--bg);box-shadow:0 20px 60px rgba(6,30,51,.3);overflow:hidden;animation:pop 200ms ease-out both}
.sheet-h{display:flex;align-items:flex-start;gap:12px;padding:16px 18px;border-bottom:1px solid var(--border)}
.sheet-h .i{width:22px;height:22px;margin-top:2px;color:var(--primary)}
.sheet-h h2{margin:0;font-size:18px}
.sheet-h p{margin:2px 0 0;color:var(--muted);font-size:13.5px}
.sheet-x{margin-left:auto;padding:4px;border-radius:8px;color:var(--muted)}
.sheet-x:hover{background:var(--hover);color:var(--text)}
.sheet-b{padding:14px 18px 18px}
.sheet-l{margin:14px 0 6px;font-size:11.5px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.sheet-l:first-child{margin-top:0}
.path{display:flex;flex-wrap:wrap;align-items:center;gap:4px;font-size:12.5px}
.path span{padding:3px 8px;border-radius:6px;background:var(--subtle);font-weight:600}
.path .i{width:13px;height:13px;color:var(--faint)}
.steps{margin:0;padding-left:18px;display:grid;gap:5px;font-size:13.5px}
.steps li::marker{color:var(--primary);font-weight:700}

/* ===== Mock primitives (mirror Open WebUI) ===== */
.mk{font-size:13px;color:var(--text)}
.hl{position:relative;border-radius:8px;box-shadow:0 0 0 2px var(--turq),0 0 0 6px var(--ring)!important;z-index:2}
[data-a]{cursor:pointer}
.m-home{display:flex;flex-direction:column;align-items:center;width:100%;max-width:640px}
.m-title{display:flex;align-items:center;gap:10px;margin:4px 0 18px;padding:2px 6px;font-size:22px}
.m-oi{display:grid;width:30px;height:30px;place-items:center;border:1px solid var(--border);border-radius:8px;background:var(--surface);font-size:11px;font-weight:800}
.m-comp{position:relative;width:100%;max-width:640px}
.m-box{padding:12px 10px 8px 14px;border:1px solid var(--border);border-radius:24px;background:var(--surface);box-shadow:0 2px 10px rgba(0,0,0,.06)}
.m-ph{padding:0 2px;font-size:15px;color:var(--muted)}
.m-ph.typed{color:var(--text)}
.m-row{display:flex;align-items:center;gap:2px;margin-top:10px}
.m-ib{display:grid;width:30px;height:30px;place-items:center;border-radius:50%;color:var(--muted)}
.m-ib .i{width:17px;height:17px}
.m-div{width:1px;height:18px;margin:0 4px;background:var(--border)}
.m-grow{flex:1}
.m-model{display:flex;align-items:center;gap:6px;padding:4px 8px;font-size:13.5px;color:var(--muted)}
.m-model .i{width:13px;height:13px}
.m-send{display:grid;width:30px;height:30px;place-items:center;border-radius:50%;background:var(--send);color:var(--send-fg);margin-left:2px}
.m-send .i{width:16px;height:16px}
.m-menu{position:absolute;top:calc(100% + 6px);min-width:270px;padding:4px;border:1px solid var(--border);border-radius:14px;background:var(--surface);box-shadow:0 8px 28px rgba(0,0,0,.10);z-index:3;animation:pop 180ms ease-out both}
.m-menu.left{left:6px}.m-menu.right{right:6px}
.m-it{display:flex;align-items:center;gap:10px;padding:6px 10px;border-radius:9px;font-size:13.5px;white-space:nowrap}
.m-it:hover{background:var(--hover)}
.m-it .i{color:var(--text)}
.m-it .m-end{display:flex;align-items:center;gap:8px;margin-left:auto;padding-left:14px;color:var(--faint);font-size:12.5px}
.m-it .m-count{color:var(--faint)}
.m-sep{height:1px;margin:4px 8px;background:var(--border)}
.m-sw{position:relative;width:28px;height:16px;border-radius:999px;background:var(--border-strong)}
.m-sw:after{content:"";position:absolute;top:2px;left:2px;width:12px;height:12px;border-radius:50%;background:#fff;transition:all 160ms ease-out}
.m-sw.on{background:var(--turq)}.m-sw.on:after{left:14px}
.m-sub{position:absolute;top:calc(100% + 6px);min-width:240px;padding:4px;border:1px solid var(--border);border-radius:14px;background:var(--surface);box-shadow:0 8px 28px rgba(0,0,0,.12);z-index:4;animation:pop 180ms ease-out both}
.m-sub .m-back{display:flex;align-items:center;gap:8px;padding:6px 10px;font-size:13.5px}
.m-subsearch{display:flex;align-items:center;gap:8px;margin:2px 4px 4px;padding:6px 8px;border-bottom:1px solid var(--border);color:var(--faint);font-size:13px}
.m-sugg{align-self:flex-start;display:grid;gap:10px;margin:18px 0 0 60px;border-radius:10px;padding:4px 8px}
.m-sugg .m-sh{display:flex;align-items:center;gap:6px;font-size:12.5px;color:var(--muted)}
.m-sugg b{display:block;font-weight:500;font-size:14px}
.m-sugg span{font-size:12px;color:var(--muted)}

/* response + feedback */
.m-resp{width:100%;max-width:640px}
.m-lines{display:grid;gap:8px;margin-bottom:12px}
.m-l{height:8px;border-radius:4px;background:var(--border)}
.m-acts{display:flex;flex-wrap:wrap;align-items:center;gap:1px}
.m-acts .m-ib{width:28px;height:28px;border-radius:8px}
.m-acts .m-ib .i{width:16px;height:16px}
.m-acts .m-ib.sel{background:var(--subtle);color:var(--text)}
.m-time{margin-left:6px;font-size:12px;color:var(--faint)}
.m-fb{margin-top:10px;padding:14px 16px;border:1px solid var(--border);border-radius:16px;background:var(--surface)}
.m-fbh{display:flex;justify-content:space-between;font-size:13.5px}
.m-scale{display:flex;justify-content:center;gap:6px;margin:10px 0 2px;padding:3px}
.m-scale span{display:grid;width:26px;height:26px;place-items:center;border:1px solid var(--border);border-radius:50%;font-size:12.5px}
.m-scale span.off{color:var(--faint)}
.m-scale span.sel{background:var(--bleu);border-color:var(--bleu);color:#fff}
.m-scalel{display:flex;justify-content:space-between;max-width:330px;margin:0 auto;font-size:11.5px;color:var(--muted)}
.m-why{margin:8px 0 6px;font-size:13.5px}
.m-chips{display:flex;flex-wrap:wrap;gap:6px;padding:3px}
.m-chips span{padding:4px 11px;border:1px solid var(--border);border-radius:999px;font-size:12.5px}
.m-chips span.sel{border-color:var(--bleu);background:var(--bleu-tint);color:var(--bleu)}
@media(prefers-color-scheme:dark){.m-chips span.sel{color:var(--turq);border-color:var(--turq)}}
.m-details{margin-top:8px;padding:6px 4px;min-height:44px;font-size:13px;color:var(--faint)}
.m-fbf{display:flex;align-items:center;justify-content:space-between;margin-top:4px}
.m-tags{display:flex;align-items:center;gap:8px;padding:3px;font-size:12px;color:var(--faint)}
.m-tags b{padding:2px 8px;border:1px solid var(--border);border-radius:999px;background:var(--subtle);font-weight:500;color:var(--text)}
.m-save{padding:6px 16px;border-radius:999px;background:var(--send);color:var(--send-fg);font-size:13px;font-weight:600}

/* sidebar */
.m-app{display:grid;grid-template-columns:230px 1fr;width:100%;max-width:720px;min-height:340px;overflow:hidden;border:1px solid var(--border);border-radius:14px;background:var(--surface)}
.m-side{display:flex;flex-direction:column;gap:1px;padding:10px 8px;background:var(--subtle)}
.m-sh1{display:flex;align-items:center;gap:8px;padding:4px 8px 10px;font-size:14px;font-weight:500}
.m-sh1 .m-oi{width:24px;height:24px;font-size:9px}
.m-sh1 .m-ib{margin-left:auto;width:26px;height:26px}
.m-si{display:flex;align-items:center;gap:10px;padding:6px 8px;border-radius:9px;font-size:14px}
.m-si .i{width:17px;height:17px}
.m-sg{display:flex;align-items:center;gap:4px;margin-top:10px;padding:5px 8px;border-radius:8px;font-size:13.5px;color:var(--faint)}
.m-sg .m-end{margin-left:auto;display:flex;gap:6px}
.m-sg .i{width:14px;height:14px}
.m-sc{padding:5px 8px 5px 12px;font-size:13px;border-radius:8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.m-mainpane{display:flex;align-items:center;justify-content:center;padding:20px;color:var(--faint);font-size:13px}

/* notes */
.m-page{width:100%;max-width:760px;border:1px solid var(--border);border-radius:14px;background:var(--surface);padding:14px 16px;overflow:hidden}
.m-nh{display:flex;align-items:center;justify-content:space-between}
.m-nh .t{padding:2px 4px;font-size:14px}.m-nh .t span{color:var(--faint);margin-left:6px}
.m-create{display:flex;align-items:center;border:1px solid var(--border);border-radius:9px;font-size:12.5px}
.m-create span{padding:4px 10px}.m-create i{display:grid;place-items:center;padding:4px 6px;border-left:1px solid var(--border)}
.m-ns{display:flex;align-items:center;justify-content:space-between;margin-top:10px}
.m-ns .s{display:flex;align-items:center;gap:8px;padding:4px;color:var(--faint);font-size:14px}
.m-ns .f{display:flex;gap:12px;padding:4px;font-size:13px}
.m-ns .f span{display:flex;align-items:center;gap:3px}
.m-ns .f .i{width:13px;height:13px}
.m-th{display:flex;justify-content:space-between;margin-top:10px;padding:4px;font-size:12.5px;color:var(--faint)}
.m-tg{margin:8px 4px 2px;font-size:12.5px;color:var(--faint)}
.m-tr{display:flex;align-items:center;gap:8px;padding:7px 4px;border-radius:8px;font-size:13.5px}
.m-tr small{color:var(--faint);font-size:11.5px}
.m-tr .r{margin-left:auto;display:flex;gap:14px;color:var(--faint);font-size:12px}
.m-editor{display:grid;grid-template-columns:1fr 250px;width:100%;max-width:780px;height:350px;border:1px solid var(--border);border-radius:14px;background:var(--surface);overflow:hidden}
.m-ed{position:relative;padding:12px 14px;border-right:1px solid var(--border)}
.m-edt{display:flex;align-items:flex-start;justify-content:space-between}
.m-edt .d{padding:2px 4px;font-size:15px}
.m-edt .tools{display:flex;align-items:center;gap:2px}
.m-edt .tools .m-ib{width:26px;height:26px}.m-edt .tools .m-ib .i{width:15px;height:15px}
.m-acc{display:flex;align-items:center;gap:4px;margin-left:4px;padding:3px 8px;border:1px solid var(--border);border-radius:8px;font-size:12.5px}
.m-acc .i{width:12px;height:12px}
.m-meta{padding:2px 4px;font-size:12px;color:var(--faint)}
.m-body{margin-top:8px;padding:4px;color:var(--muted);font-size:14px}
.m-edchat{display:flex;flex-direction:column;padding:10px 10px 8px}
.m-edchat .h{display:flex;justify-content:space-between;font-size:13px;color:var(--muted)}
.m-sp{margin-top:auto;display:grid;gap:9px;padding:6px;border-radius:8px;font-size:12px;color:var(--muted)}
.m-sp small{color:var(--faint)}
.m-mini{margin-top:10px;padding:8px 8px 6px 10px;border:1px solid var(--border);border-radius:16px;box-shadow:0 2px 8px rgba(0,0,0,.05)}
.m-mini .ph{font-size:13px;color:var(--muted)}
.m-mini .m-row{margin-top:6px}
.m-mini .m-ib{width:24px;height:24px}.m-mini .m-ib .i{width:14px;height:14px}
.m-mini .m-model{font-size:11.5px;padding:2px 4px}
.m-mini .up{display:grid;width:24px;height:24px;place-items:center;border-radius:50%;background:var(--border);color:#fff}

/* folder modal */
.m-modal{width:100%;max-width:560px;padding:18px 20px;border-radius:26px;background:var(--surface);box-shadow:0 10px 40px rgba(0,0,0,.15)}
.m-modal .h{display:flex;justify-content:space-between;font-size:15px;margin-bottom:12px}
.m-fl{padding:3px 4px;border-radius:8px}
.m-fl label{display:block;font-size:12.5px;color:var(--muted)}
.m-fl .v{font-size:14px;color:var(--faint)}
.m-flr{display:flex;justify-content:space-between;align-items:center}
.m-hr{height:1px;margin:10px 0;background:var(--border)}
.m-kn{display:flex;gap:10px;font-size:12.5px;color:var(--muted)}
.m-kn span:first-child{color:var(--muted)}
.m-knnote{margin-top:6px;font-size:12.5px}

/* channels */
.m-chan{display:grid;grid-template-columns:180px 1fr;width:100%;max-width:760px;height:350px;border:1px solid var(--border);border-radius:14px;background:var(--surface);overflow:hidden}
.m-cmain{display:flex;flex-direction:column;padding:10px 14px}
.m-chead{display:flex;align-items:center;justify-content:space-between;padding:2px 4px 8px;border-bottom:1px solid var(--border);font-size:14px;font-weight:600}
.m-msg{display:flex;gap:10px;margin-top:10px;padding:4px;border-radius:10px}
.m-av{display:grid;width:28px;height:28px;flex:0 0 auto;place-items:center;border-radius:50%;background:var(--bleu-tint);color:var(--bleu);font-size:11px;font-weight:700}
.m-av.bot{background:var(--bleu);color:#fff}
.m-msg .n{font-size:13px;font-weight:600}.m-msg .n small{margin-left:6px;color:var(--faint);font-weight:400}
.m-msg .x{font-size:13px}
.m-mention{color:var(--bleu);font-weight:600;background:var(--bleu-tint);padding:0 4px;border-radius:4px}
@media(prefers-color-scheme:dark){.m-mention{color:var(--turq)}}
.m-under{display:flex;gap:8px;margin-top:4px}
.m-pill{display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border:1px solid var(--border);border-radius:999px;font-size:11.5px;color:var(--muted)}
.m-pill .i{width:12px;height:12px}
.m-cin{margin-top:auto;padding:8px 10px;border:1px solid var(--border);border-radius:16px;display:flex;align-items:center;gap:8px;color:var(--muted);font-size:13px}

/* do / don't */
.m-cols{display:grid;grid-template-columns:1fr 1fr;gap:10px;width:100%;max-width:640px}
.m-card{padding:14px;border:1px solid var(--border);border-radius:12px;background:var(--surface)}
.m-card h3{margin:0 0 8px;font-size:13px}
.m-rule{display:flex;gap:8px;margin-top:8px;font-size:13px}
.m-rule.ok .i{color:var(--turq)}.m-rule.no .i{color:var(--orange)}

/* cover */
.cover{margin-top:18px;padding:22px;border-radius:14px;background:var(--bleu);color:#fff}
@media(prefers-color-scheme:dark){.cover{background:#122C42}}
.cover h2{margin:0 0 6px;font-size:18px}
.cover p{margin:0;color:rgba(255,255,255,.82);font-size:14px;max-width:640px}
.facts{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}
.fact{display:flex;align-items:center;gap:6px;padding:5px 10px;border-radius:999px;background:rgba(255,255,255,.12);font-size:12.5px;font-weight:600}
.fact .i{width:14px;height:14px;color:var(--jaune)}
.cover-actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:16px}
.cbtn{display:inline-flex;align-items:center;gap:6px;padding:8px 14px;border-radius:10px;font-size:14px;font-weight:700;transition:all 160ms ease-out}
.cbtn.main{background:var(--jaune);color:var(--nuit)}.cbtn.main:hover{filter:brightness(.95)}
.cbtn.alt{color:#fff;border:1px solid rgba(255,255,255,.4)}.cbtn.alt:hover{background:rgba(255,255,255,.1)}
.toc-h{margin:20px 0 8px;font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.toc{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}
.toc button{display:flex;gap:10px;padding:12px;border:1px solid var(--border);border-radius:12px;transition:all 160ms ease-out}
.toc button:hover{border-color:var(--turq);background:var(--turq-tint)}
.toc .i{width:18px;height:18px;margin-top:1px;color:var(--primary)}
.toc b{display:block;font-size:13.5px}.toc span{display:block;font-size:12px;color:var(--muted)}
.toc small{display:block;margin-top:4px;font-size:11px;color:var(--faint)}
/* user menu */
.m-user{position:relative;width:250px;padding:4px;border:1px solid var(--border);border-radius:14px;background:var(--surface);box-shadow:0 8px 28px rgba(0,0,0,.10)}
.m-uh{display:flex;align-items:center;gap:8px;padding:6px 10px;font-size:13.5px}
.m-uh .dot{width:7px;height:7px;border-radius:50%;background:#22C55E;margin-left:auto}
.m-uh small{color:var(--faint);font-size:12px}
.m-face{display:grid;width:20px;height:20px;place-items:center;border-radius:50%;background:var(--bleu);color:#fff;font-size:10px;font-weight:700}
/* calendar */
.m-calapp{display:grid;grid-template-columns:180px 1fr;width:100%;max-width:780px;min-height:380px;border:1px solid var(--border);border-radius:14px;background:var(--surface);overflow:hidden}
.m-calside{display:flex;flex-direction:column;gap:10px;padding:12px;border-right:1px solid var(--border)}
.m-newev{display:flex;align-items:center;justify-content:center;gap:6px;padding:7px;border:1px solid var(--border);border-radius:10px;font-size:13px;font-weight:600}
.m-mini{display:grid;grid-template-columns:repeat(7,1fr);gap:2px;padding:4px;border-radius:8px;font-size:10px;text-align:center;color:var(--muted)}
.m-mini span.t{background:var(--bleu);color:#fff;border-radius:50%}
.m-calh{display:flex;align-items:center;justify-content:space-between;padding:3px 4px;font-size:12px;color:var(--muted)}
.m-calrow{display:flex;align-items:center;gap:8px;padding:4px;border-radius:8px;font-size:12.5px}
.m-sq{width:10px;height:10px;border-radius:3px}
.m-calmain{display:flex;flex-direction:column;padding:10px 12px}
.m-calbar{display:flex;align-items:center;gap:8px;padding-bottom:8px}
.m-calbar .m-btn2{padding:4px 10px;border:1px solid var(--border);border-radius:8px;font-size:12.5px}
.m-calbar .nav{display:flex;padding:2px;border-radius:8px}
.m-calbar .nav .i{width:16px;height:16px;color:var(--muted)}
.m-calbar h4{margin:0;font-size:15px;font-weight:600}
.m-calbar .view{margin-left:auto;display:flex;align-items:center;gap:4px;padding:4px 10px;border:1px solid var(--border);border-radius:8px;font-size:12.5px}
.m-grid7{display:grid;grid-template-columns:repeat(7,1fr);flex:1;border-top:1px solid var(--border);border-left:1px solid var(--border)}
.m-grid7 div{min-height:52px;padding:3px;border-right:1px solid var(--border);border-bottom:1px solid var(--border);font-size:10.5px;color:var(--muted)}
.m-grid7 .hd{min-height:0;padding:4px;font-weight:600;text-align:center}
.m-ev{display:block;margin-top:2px;padding:1px 4px;border-radius:4px;font-size:10px;color:#fff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.m-ev.b{background:#3B82F6}.m-ev.v{background:#8B5CF6}.m-ev.g{background:#10B981}
.m-form{width:100%;max-width:520px;padding:16px 18px;border-radius:20px;background:var(--surface);box-shadow:0 10px 40px rgba(0,0,0,.15)}
.m-form .h{display:flex;justify-content:space-between;margin-bottom:8px;font-size:15px;font-weight:600}
.m-f{display:flex;align-items:center;gap:10px;padding:6px 4px;border-radius:8px;font-size:13px}
.m-f .i{color:var(--muted)}
.m-f .lbl{width:84px;flex:0 0 auto;color:var(--muted);font-size:12.5px}
.m-f .val{flex:1;padding:5px 8px;border:1px solid var(--border);border-radius:8px;color:var(--text)}
.m-f .val.ph{color:var(--faint)}
.m-seg{display:flex;flex-wrap:wrap;gap:4px}
.m-seg span{padding:3px 9px;border:1px solid var(--border);border-radius:999px;font-size:12px}
.m-seg span.sel{background:var(--bleu);border-color:var(--bleu);color:#fff}
/* automations */
.m-rowx{display:flex;align-items:center;gap:10px;padding:9px 6px;border-bottom:1px solid var(--border);border-radius:8px;font-size:13px}
.m-rowx small{display:block;color:var(--faint);font-size:11.5px}
.m-rowx .m-sw{margin-left:auto}
.m-split{display:flex;align-items:center;border-radius:9px;background:var(--send);color:var(--send-fg);font-size:12.5px}
.m-split span{padding:4px 10px}.m-split i{display:grid;place-items:center;padding:4px 6px;border-left:1px solid rgba(255,255,255,.25)}
.m-split .i{width:13px;height:13px}
.m-tbtns{display:flex;gap:6px}
.m-tbtns span{display:flex;align-items:center;gap:4px;padding:4px 10px;border:1px solid var(--border);border-radius:8px;font-size:12.5px}
.m-tbtns .i{width:13px;height:13px}
.m-tbtns .run{background:var(--bleu);border-color:var(--bleu);color:#fff}
.m-log{display:flex;align-items:center;gap:8px;padding:5px 4px;font-size:12px;color:var(--muted)}
.m-log .ok{color:#10B981}.m-log .err{color:var(--orange)}
.m-log a{margin-left:auto;color:var(--primary);font-weight:600}
/* built-in tools */
.m-tc{display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border:1px solid var(--border);border-radius:999px;background:var(--subtle);font-size:12px;color:var(--muted)}
.m-tc .i{width:13px;height:13px;color:#10B981}
.m-bt{display:grid;grid-template-columns:repeat(3,1fr);gap:4px;margin-top:14px;padding:8px;border:1px solid var(--border);border-radius:12px;background:var(--surface)}
.m-bt .m-it{white-space:normal;font-size:12.5px;padding:6px 8px}
.m-bt .m-it .i{color:var(--primary)}
@media(max-width:760px){.toc{grid-template-columns:1fr 1fr}.m-calapp{grid-template-columns:1fr}.m-calside{display:none}.m-bt{grid-template-columns:1fr 1fr}}
@media(max-width:520px){.toc{grid-template-columns:1fr}}
/* dismissed / toast */
.g.slim .g-chapters,.g.slim .g-bar,.g.slim .g-body,.g.slim .g-tabs{display:none}
.slim-msg{font-size:13.5px}
.toast{position:fixed;left:50%;bottom:14px;padding:9px 14px;border-radius:10px;background:var(--nuit);color:#fff;font-size:13px;opacity:0;transform:translate(-50%,8px);pointer-events:none;transition:all 160ms ease-out;z-index:50}
.toast.show{opacity:1;transform:translate(-50%,0)}

@keyframes fade{from{opacity:0;transform:translateY(3px)}to{opacity:1;transform:none}}
@keyframes fadeonly{from{opacity:0}to{opacity:1}}
@keyframes pop{from{opacity:0;transform:translateY(-4px)}to{opacity:1;transform:none}}

@media(max-width:760px){
  .notes,.compare{grid-template-columns:1fr}
  .lib-grid{grid-template-columns:1fr 1fr}
  .m-app{grid-template-columns:200px 1fr}
  .m-editor{grid-template-columns:1fr}.m-edchat{display:none}
  .m-chan{grid-template-columns:1fr}.m-chan .m-side{display:none}
  .g-name span{display:none}
}
@media(max-width:520px){
  .g-head{flex-wrap:wrap;gap:8px}.g-tabs{order:3;margin-left:0;width:100%}
  .lib-grid,.m-cols{grid-template-columns:1fr}
  .stage{padding:14px 10px;overflow-x:auto;justify-content:flex-start}
  .m-app{grid-template-columns:1fr}.m-app .m-mainpane{display:none}
  .m-ns .f{display:none}
  .m-sub{left:36px!important;top:calc(100% + 40px)!important;box-shadow:0 12px 36px rgba(0,0,0,.22)}
}
@media(prefers-reduced-motion:reduce){*,*:before,*:after{animation:none!important;transition:none!important}}
</style>
</head>
<body>
<section class="g" id="g" aria-label="Guide">
  <header class="g-head">
    <span class="g-logo" id="gLogo" aria-hidden="true"></span>
    <span class="g-name" id="gName"></span>
    <nav class="g-tabs" role="tablist">
      <button class="g-tab" role="tab" data-tab="tour" type="button"></button>
      <button class="g-tab" role="tab" data-tab="lib" type="button"></button>
    </nav>
    <div class="g-right">
      <div class="g-lang" role="group" aria-label="Langue / Language"><button type="button" data-lang="fr">FR</button><button type="button" data-lang="en">EN</button></div>
      <button class="g-close" id="close" type="button"></button>
    </div>
  </header>
  <div class="g-body" id="gBody"></div>
</section>
<div class="toast" id="toast" role="status" aria-live="polite"></div>
<script type="application/json" id="snapshot">__SNAPSHOT_JSON__</script>
<script>
(function(){
'use strict';
var data;try{data=JSON.parse(document.getElementById('snapshot').textContent);}catch(e){document.body.textContent='Onboarding data could not be loaded.';return;}
var ui=data.ui||{},brand=data.brand||{},res=data.resources||{},acc=data.access||{},loc=data.localization||{};
var PRODUCT=brand.product||'AI Assistant',CHD={};
var SUPPORTED=Array.isArray(loc.supported)&&loc.supported.length?loc.supported:['fr','en'],TRANSLATIONS=loc.translations||{},LOCALE_NAMES=loc.names||{};
var initialLang=SUPPORTED.indexOf(ui.default_language)>=0?ui.default_language:(SUPPORTED.indexOf('en')>=0?'en':SUPPORTED[0]);
var updated=false,REV=Number(data.guide_revision||1)+'.'+Number(data.template_revision||0),lang=initialLang,tab='tour',cur=0,active=null,steps=[],chapters=[],sheet=null,toastT;
var KEY='openwebui-onboarding-v7-'+String(data.progress_scope||'local'),mem={};

function L(fr,en){if(lang==='fr')return fr;if(lang==='en')return en;var table=TRANSLATIONS[lang]||{},key=fr+'\u001f'+en;return Object.prototype.hasOwnProperty.call(table,key)?table[key]:en;}
function E(t,c,x){var n=document.createElement(t);if(c)n.className=c;if(x!==undefined)n.textContent=String(x);return n;}
function add(p){for(var i=1;i<arguments.length;i++){var a=arguments[i];if(a===null||a===undefined||a===false)continue;p.appendChild(typeof a==='string'?document.createTextNode(a):a);}return p;}
function on(k){return !!ui[k];}
var ADMIN=!!ui.is_admin;function ft(k){var a=res.features||[];for(var i=0;i<a.length;i++)if(a[i].key===k)return !!a[i].enabled;return false;}
function av(k){return !!(data.available&&data.available[k]);}
function ws(k){return !!(acc.workspace&&acc.workspace[k]);}
function anyWs(){return ws('models')||ws('knowledge')||ws('prompts')||ws('tools')||ws('skills');}
var TOOLS=res.tools||[],TOGGLES=res.toggles||[],ACTIONS=res.actions||[];
var hasTools=TOOLS.length>0||av('tools');
function load(){try{var r=localStorage.getItem(KEY);if(r)return JSON.parse(r);}catch(e){}return mem;}
function save(p){mem=Object.assign({},load(),p);try{localStorage.setItem(KEY,JSON.stringify(mem));}catch(e){}}
function height(){try{parent.postMessage({type:'iframe:height',height:Math.ceil(document.documentElement.scrollHeight)},'*');}catch(e){}}
function sendPrompt(t){try{parent.postMessage({type:'input:prompt',text:String(t).slice(0,4000)},'*');}catch(e){}var n=document.getElementById('toast');n.textContent=L('Exemple ajouté dans la zone de saisie.','Example added to the message box.');n.classList.add('show');clearTimeout(toastT);toastT=setTimeout(function(){n.classList.remove('show');},2200);}

var P={
 plus:'M12 5v14|M5 12h14',integ:'M12 3.5l2.2 2.2L12 7.9 9.8 5.7z|M5.7 9.8l2.2 2.2-2.2 2.2L3.5 12z|M18.3 9.8l2.2 2.2-2.2 2.2-2.2-2.2z|M12 16.1l2.2 2.2-2.2 2.2-2.2-2.2z',
 cloud:'M17.5 19H9a7 7 0 1 1 6.7-9h1.8a4.5 4.5 0 1 1 0 9z',down:'M6 9l6 6 6-6',right:'M9 6l6 6-6 6',left:'M15 6l-6 6 6 6',arrowr:'M5 12h14|M13 6l6 6-6 6',arrowl:'M19 12H5|M11 6l-6 6 6 6',
 x:'M18 6 6 18|M6 6l12 12',check:'M20 6 9 17l-5-5',mic:'M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z|M19 10v2a7 7 0 0 1-14 0v-2|M12 19v3',wave:'M4 10v4|M8 7v10|M12 4v16|M16 7v10|M20 10v4',
 shield:'M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z',clip:'M21.4 11.1l-9.2 9.2a6 6 0 0 1-8.5-8.5l9.2-9.2a4 4 0 0 1 5.7 5.7l-9.2 9.2a2 2 0 0 1-2.8-2.8l8.5-8.5',
 camera:'M4 8h3l2-3h6l2 3h3a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1z|M12 17a4 4 0 1 0 0-8 4 4 0 0 0 0 8z',
 globe:'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z|M2 12h20|M12 2a15 15 0 0 1 0 20a15 15 0 0 1 0-20z',fileplus:'M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z|M14 3v5h5|M12 11v6|M9 14h6',
 notefile:'M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z|M14 3v5h5|M9 13h6|M9 17h4',db:'M12 8c4.4 0 8-1.3 8-3s-3.6-3-8-3-8 1.3-8 3 3.6 3 8 3z|M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5|M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3',
 history:'M3 12a9 9 0 1 0 3-6.7L3 8|M3 3v5h5|M12 7v5l3 2',wrench:'M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.5-.5-.5-2.5z',
 sliders:'M4 6h9|M17 6h3|M4 12h3|M11 12h9|M4 18h11|M19 18h1|M15 4v4|M9 10v4|M17 16v4',terminal:'M4 4h16v16H4z|M8 9l3 3-3 3|M13 15h3',image:'M3 3h18v18H3z|M21 15l-5-5L5 21|M9 10a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3z',
 pencil:'M17 3a2.8 2.8 0 0 1 4 4L7.5 20.5 2 22l1.5-5.5z',clipboard:'M9 4h6v3H9z|M15 5h3v16H6V5h3',speaker:'M11 5 6 9H2v6h4l5 4z|M15.5 8.5a5 5 0 0 1 0 7|M19 5a10 10 0 0 1 0 14',
 info:'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z|M12 16v-4|M12 8h.01',tup:'M7 10v11|M15 5.9 14 10h5.8a2 2 0 0 1 2 2.3l-1.4 7A2 2 0 0 1 18.4 21H7V10l4-8a3 3 0 0 1 4 3.9z',
 tdown:'M17 14V3|M9 18.1 10 14H4.2a2 2 0 0 1-2-2.3l1.4-7A2 2 0 0 1 5.6 3H17v11l-4 8a3 3 0 0 1-4-3.9z',play:'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z|M10 8l6 4-6 4z',
 refresh:'M3 12a9 9 0 0 1 15-6.7L21 8|M21 3v5h-5|M21 12a9 9 0 0 1-15 6.7L3 16|M3 21v-5h5',doc:'M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z|M14 3v5h5|M9 13h6|M9 17h6',
 spark:'M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z|M19 16l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z',edit:'M12 20h9|M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z',
 search:'M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16z|M21 21l-4.3-4.3',book:'M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5z|M4 19.5V21h16',
 grid:'M4 4h6v6H4z|M14 4h6v6h-6z|M4 14h6v6H4z|M17 14v6|M14 17h6',panel:'M3 4h18v16H3z|M9 4v16',dots:'M5 12h.01|M12 12h.01|M19 12h.01',
 folder:'M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z',hash:'M4 9h16|M4 15h16|M10 3 8 21|M16 3l-2 18',users:'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2|M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z|M22 21v-2a4 4 0 0 0-3-3.9|M16 3.1a4 4 0 0 1 0 7.8',
 undo:'M9 14 4 9l5-5|M4 9h11a5 5 0 0 1 0 10h-3',redo:'M15 14l5-5-5-5|M20 9H9a5 5 0 0 0 0 10h3',bubble:'M21 12a8 8 0 0 1-11.6 7.1L3 21l1.9-6.4A8 8 0 1 1 21 12z',
 lock:'M5 11h14v10H5z|M8 11V7a4 4 0 0 1 8 0v4',download:'M12 3v12|M7 10l5 5 5-5|M5 21h14',upcloud:'M12 13v8|M8 17l4-4 4 4|M20 16.6A5 5 0 0 0 18 7h-1.3A8 8 0 1 0 4 15.3',
 share:'M12 3v12|M7 8l5-5 5 5|M5 14v6h14v-6',pin:'M12 17v5|M9 3h6l-1 7 4 3v2H6v-2l4-3z',trash:'M3 6h18|M8 6V4h8v2|M6 6l1 15h10l1-15',
 bulb:'M9 18h6|M10 22h4|M12 2a7 7 0 0 0-4 12.7V16h8v-1.3A7 7 0 0 0 12 2z',warn:'M12 3 2 21h20z|M12 10v5|M12 18h.01',bolt:'M13 2 4 14h7l-1 8 9-12h-7z',
 okc:'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z|M8 12l3 3 5-6',noc:'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z|M15 9l-6 6|M9 9l6 6',
 brain:'M12 5a3 3 0 1 0-5.9.9A4 4 0 0 0 4 12a4 4 0 0 0 2 3.5A3.5 3.5 0 0 0 12 18z|M12 5a3 3 0 1 1 5.9.9A4 4 0 0 1 20 12a4 4 0 0 1-2 3.5A3.5 3.5 0 0 1 12 18z|M12 5v13',
 layers:'M12 2 2 7l10 5 10-5z|M2 17l10 5 10-5|M2 12l10 5 10-5',slash:'M16 3 8 21',smile:'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z|M8 14s1.5 2 4 2 4-2 4-2|M9 9h.01|M15 9h.01',reply:'M9 17l-5-5 5-5|M20 18v-2a4 4 0 0 0-4-4H4',
 imageup:'M21 12v7H3V5h9|M16 5h6|M19 2v6|M3 16l5-5 5 5',calendar:'M3 5h18v16H3z|M16 3v4|M8 3v4|M3 10h18',clock:'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z|M12 6v6l4 2',code:'M8 8l-4 4 4 4|M16 8l4 4-4 4',user:'M20 21a8 8 0 0 0-16 0|M12 13a5 5 0 1 0 0-10 5 5 0 0 0 0 10z',gear:'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z|M19.4 15l1.5 1.2-2 3.4-1.8-.6a7 7 0 0 1-2.1 1.2L14.6 22h-4l-.4-1.8a7 7 0 0 1-2.1-1.2l-1.8.6-2-3.4L5.8 15a7 7 0 0 1 0-2.4L4.3 11.4l2-3.4 1.8.6a7 7 0 0 1 2.1-1.2L10.6 5.6h4l.4 1.8a7 7 0 0 1 2.1 1.2l1.8-.6 2 3.4-1.5 1.2a7 7 0 0 1 0 2.4z',logout:'M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4|M16 17l5-5-5-5|M21 12H9',pause:'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z|M10 9v6|M14 9v6',repeat:'M17 2l4 4-4 4|M3 11v-1a4 4 0 0 1 4-4h14|M7 22l-4-4 4-4|M21 13v1a4 4 0 0 1-4 4H3',bell:'M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9|M13.7 21a2 2 0 0 1-3.4 0',mappin:'M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0z|M12 13a3 3 0 1 0 0-6 3 3 0 0 0 0 6z',tasks:'M9 6h11|M9 12h11|M9 18h11|M4 6l1 1 2-2|M4 12l1 1 2-2|M4 18l1 1 2-2',flask:'M9 3h6|M10 3v6L4 20h16L14 9V3',map:'M9 4 3 6v14l6-2 6 2 6-2V4l-6 2z|M9 4v14|M15 6v14'
};
function I(n){var s=document.createElementNS('http://www.w3.org/2000/svg','svg');s.setAttribute('viewBox','0 0 24 24');s.setAttribute('aria-hidden','true');s.setAttribute('class','i');String(P[n]||P.info).split('|').forEach(function(d){var p=document.createElementNS('http://www.w3.org/2000/svg','path');p.setAttribute('d',d);s.appendChild(p);});return s;}

/* Register an annotated element in the mock. Clicking it selects the matching explanation. */
function A(id,el){el.setAttribute('data-a',id);if(active===id)el.classList.add('hl');return el;}

/* ============ Mock builders (mirror the real screens) ============ */
function mItem(id,icon,label,end){var r=E('div','m-it');if(icon)add(r,I(icon));add(r,E('span','',label));if(end){var e=E('span','m-end');end.forEach(function(x){add(e,x);});add(r,e);}return id?A(id,r):r;}
function sw(onn){return E('span','m-sw'+(onn?' on':''));}
function composer(o){
  o=o||{};var c=E('div','m-comp'),b=E('div','m-box'),row=E('div','m-row');
  add(b,A('input',E('div','m-ph'+(o.text?' typed':''),o.text||'How can I help you today?')));
  add(row,A('plus',add(E('span','m-ib'),I('plus'))),E('span','m-div'),A('integ',add(E('span','m-ib'),I('integ'))),E('span','m-grow'),
      A('model',add(E('span','m-model'),E('span','',o.model||PRODUCT),I('down'))));
  if(ft('stt'))add(row,A('mic',add(E('span','m-ib'),I('mic'))));
  add(row,A('voice',add(E('span','m-send'),I('wave'))));
  add(b,row);add(c,b);if(o.menu)add(c,o.menu);return c;
}
function home(o){o=o||{};var h=E('div','m-home');add(h,A('title',add(E('div','m-title'),E('span','m-oi','OI'),E('span','',PRODUCT))),composer(o));
  if(o.sugg!==false){var s=A('sugg',E('div','m-sugg'));add(s,add(E('div','m-sh'),I('bolt'),E('span','','Suggested')));
    [[L('Résumer un document','Summarize a document'),L('En 5 points','In 5 points')],[L('Comparer deux options','Compare two options'),L('Avantages et limites','Pros and cons')],[L('Rédiger un e-mail','Draft an email'),L('Clair et professionnel','Clear and professional')]].forEach(function(x){add(s,add(E('div'),E('b','',x[0]),E('span','',x[1])));});add(h,s);}
  return h;}

function plusItems(){var it=[];
  if(hasTools)it.push({id:'toolperm',i:'shield',l:'Tool Permissions',end:'Full access',arrow:1,sep:1});
  if(ft('file_upload')){it.push({id:'upload',i:'clip',l:'Upload Files'});it.push({id:'capture',i:'camera',l:'Capture'});}
  it.push({id:'webpage',i:'globe',l:'Attach Webpage'});
  if(ft('file_upload'))it.push({id:'attfiles',i:'fileplus',l:'Attach Files',arrow:1});
  if(ft('notes'))it.push({id:'attnotes',i:'notefile',l:'Attach Notes',arrow:1});
  if(av('knowledge')||ws('knowledge'))it.push({id:'attknow',i:'db',l:'Attach Knowledge',arrow:1});
  it.push({id:'refchats',i:'history',l:'Reference Chats',arrow:1});
  return it;}
function subFor(id){var s=E('div','m-sub');s.style.left='284px';
  if(id==='toolperm'){add(s,add(E('div','m-back'),I('left'),E('span','','Tool Permissions')),mItem('perm-full',null,'Full access',[I('check')]),mItem('perm-ask',null,'Ask for approval'));return s;}
  var map={attfiles:[L('Rechercher des fichiers','Search files'),['rapport-2026.pdf','planning.xlsx','note-interne.docx'],'doc'],
    attnotes:[L('Rechercher des notes','Search notes'),[L('Notes de réunion','Meeting notes'),L('Idées de projet','Project ideas')],'notefile'],
    attknow:[L('Rechercher','Search'),[L('Règlement intérieur','Internal rules'),L('Guide des outils','Tools handbook')],'db'],
    refchats:[L('Rechercher des conversations','Search chats'),[L('Plan de projet','Project plan'),L('E-mail au fournisseur','Email to supplier')],'history']};
  var m=map[id];if(!m)return null;add(s,add(E('div','m-subsearch'),I('search'),E('span','',m[0])));m[1].forEach(function(n){add(s,mItem(null,m[2],n));});return s;}
function plusMenu(){var m=E('div','m-menu left');plusItems().forEach(function(x){
  var end=[];if(x.end)end.push(E('span','',x.end));if(x.arrow)end.push(I('right'));add(m,mItem(x.id,x.i,x.l,end.length?end:null));if(x.sep)add(m,E('div','m-sep'));});
  var wrap=E('div');wrap.style.position='relative';add(wrap,m);return m;}
function integItems(){var it=[];
  if(hasTools)it.push({id:'tools',i:'wrench',l:'Tools',count:TOOLS.length||'',arrow:1});
  TOGGLES.slice(0,4).forEach(function(t,k){it.push({id:'toggle-'+k,i:'spark',l:t.name,valves:1,sw:1,desc:t.description});});
  if(ft('web_search'))it.push({id:'web',i:'globe',l:'Web Search',sw:1});
  if(ft('image_generation'))it.push({id:'imagegen',i:'image',l:'Image',sw:1});
  if(ft('code_interpreter'))it.push({id:'code',i:'terminal',l:'Code Interpreter',sw:1});
  return it;}
function integMenu(){var m=E('div','m-menu left');m.style.left='44px';integItems().forEach(function(x){var end=[];
  if(x.count!==undefined){var r=mItem(x.id,x.i,'',null);r.lastChild.remove();add(r,add(E('span'),x.l+' ',E('span','m-count',String(x.count))));add(r,add(E('span','m-end'),I('right')));add(m,r);return;}
  if(x.valves)end.push(I('sliders'));if(x.sw)end.push(sw(active===x.id));add(m,mItem(x.id,x.i,x.l,end));});return m;}
function toolsSub(){var s=E('div','m-sub');s.style.left='330px';add(s,add(E('div','m-back'),I('left'),E('span','','Tools')));
  var list=TOOLS.length?TOOLS.slice(0,7):[{name:L('Outil 1','Tool 1')},{name:L('Outil 2','Tool 2')}];
  list.forEach(function(t,k){add(s,mItem(k===0?'tool-row':null,'wrench',t.name,[sw(k===0&&active==='tool-row')]));});return s;}

function actionsRow(sel){var r=E('div','m-acts');
  [['edit','pencil'],['copy','clipboard'],['speak','speaker'],['ginfo','info'],['good','tup'],['bad','tdown'],['continue','play'],['regen','refresh']].forEach(function(x){var b=A(x[0],add(E('span','m-ib'+(sel===x[0]?' sel':'')),I(x[1])));add(r,b);});
  ACTIONS.slice(0,4).forEach(function(a,k){add(r,A('act-'+k,add(E('span','m-ib'),I(k%2?'spark':'doc'))));});
  add(r,E('span','m-time','Sep 16, 11:31 AM'));return r;}
function feedback(kind){var f=E('div','m-fb');add(f,add(E('div','m-fbh'),E('span','','How would you rate this response?'),I('x')));
  var sc=A('score',E('div','m-scale'));for(var i=1;i<=10;i++){var sp=E('span','',i);if(kind==='good'&&i<6||kind==='bad'&&i>5)sp.className='off';if(kind==='good'&&i===8||kind==='bad'&&i===3)sp.className='sel';add(sc,sp);}
  add(f,sc,add(E('div','m-scalel'),E('span','','1 - Awful'),E('span','','10 - Amazing')),E('div','m-why','Why?'));
  var reasons=kind==='good'?['Accurate information','Followed instructions perfectly','Showcased creativity','Positive attitude','Attention to detail','Thorough explanation','Other']:['Don’t like the style','Too verbose','Not helpful','Not factually correct','Didn’t fully follow instructions','Refused when it shouldn’t have','Being lazy','Other'];
  var ch=A('reasons',E('div','m-chips'));reasons.forEach(function(r,k){add(ch,E('span',(kind==='good'&&k===0)||(kind==='bad'&&k===3)?'sel':'',r));});add(f,ch);
  add(f,A('details',E('div','m-details','Feel free to add specific details')));
  add(f,add(E('div','m-fbf'),A('tags',add(E('div','m-tags'),E('b','','General ×'),E('span','','Add a tag...'))),A('save',E('span','m-save','Save'))));return f;}

function sidebar(extraChats){var s=E('div','m-side');
  add(s,add(E('div','m-sh1'),E('span','m-oi','OI'),E('span','',PRODUCT),A('collapse',add(E('span','m-ib'),I('panel')))));
  add(s,A('new',add(E('div','m-si'),I('edit'),E('span','','New Chat'))),A('search',add(E('div','m-si'),I('search'),E('span','','Search'))));
  if(ft('notes'))add(s,A('notes',add(E('div','m-si'),I('book'),E('span','','Notes'))));
  if(anyWs())add(s,A('workspace',add(E('div','m-si'),I('grid'),E('span','','Workspace'))));
  add(s,A('models',add(E('div','m-sg'),E('span','','Models'))));
  if(ft('channels'))add(s,A('channels',add(E('div','m-sg'),E('span','','Channels'),add(E('span','m-end'),I('plus')))));
  if(ft('folders'))add(s,A('folders',add(E('div','m-sg'),E('span','','Folders'),add(E('span','m-end'),I('plus')))));
  add(s,A('chats',add(E('div','m-sg'),E('span','','Chats'),I('right'),add(E('span','m-end'),I('dots')))));
  if(extraChats)[L('Plan de projet','Project plan'),L('E-mail au fournisseur','Email to supplier')].forEach(function(t){add(s,E('div','m-sc',t));});
  return s;}

function notesList(){var p=E('div','m-page');
  add(p,add(E('div','m-nh'),A('count',add(E('span','t'),'Notes',E('span','','5'))),A('create',add(E('span','m-create'),E('span','','Create'),add(E('i'),I('down'))))));
  add(p,add(E('div','m-ns'),A('nsearch',add(E('span','s'),I('search'),E('span','','Search Notes'))),A('filters',add(E('span','f'),add(E('span'),'All',I('down')),add(E('span'),'Write',I('down')),add(E('span'),'List',I('down'))))));
  add(p,A('sort',add(E('div','m-th'),E('span','','Title'),E('span','','Updated at'))),E('div','m-tg','Previous 30 days'));
  [[L('Idées et inspiration','Ideas & inspiration'),'12 days ago'],[L('Compte rendu de réunion','Meeting minutes'),'12 days ago'],[L('Plan du projet','Project outline'),'a month ago']].forEach(function(r,k){
    var row=add(E('div','m-tr'),E('span','',r[0]),E('small','',r[1]),add(E('span','r'),E('span','',L('Vous','You')),add(E('span'),I('dots'))));add(p,k===0?A('row',row):row);});
  return p;}
function noteEditor(menu){var g=E('div','m-editor'),ed=E('div','m-ed'),top=E('div','m-edt'),tools=E('div','tools');
  add(tools,A('undo',add(E('span','m-ib'),I('undo'))),add(E('span','m-ib'),I('redo')),A('nchat',add(E('span','m-ib'),I('bubble'))),A('nrec',add(E('span','m-ib'),I('mic'))),A('nmore',add(E('span','m-ib'),I('dots'))),A('access',add(E('span','m-acc'),I('lock'),E('span','','Access'))));
  add(top,A('ntitle',E('span','d','2026-09-16')),tools);add(ed,top,A('nmeta',E('div','m-meta','Today at 11:30 AM   0 words 0 characters')),A('nbody',E('div','m-body','Write something...')));
  if(menu){var m=E('div','m-menu right');m.style.top='42px';m.style.right='80px';m.style.minWidth='170px';
    [['m-download','download','Download'],['m-upload','upcloud','Upload files'],['m-share','share','Share'],['m-pin','pin','Pin to Sidebar'],['m-delete','trash','Delete']].forEach(function(x){add(m,mItem(x[0],x[1],x[2]));});add(ed,m);}
  var c=E('div','m-edchat');add(c,add(E('div','h'),E('span','','Chat'),I('x')));
  var sp=A('nprompts',E('div','m-sp'));add(sp,E('small','','Suggested prompts'),E('span','','Enhance this note and update it.'),E('span','','Summarize this note.'),E('span','','Extract action items from this note.'),E('span','','Rewrite the selected text.'));
  var mini=A('ncomposer',E('div','m-mini'));add(mini,E('div','ph','Send a Message'),add(E('div','m-row'),add(E('span','m-ib'),I('plus')),add(E('span','m-ib'),I('integ')),add(E('span','m-model'),E('span','',PRODUCT),I('down')),E('span','m-grow'),add(E('span','m-ib'),I('mic')),add(E('span','up'),I('arrowr'))));
  add(c,sp,mini);add(g,ed,c);return g;}

function folderModal(){var m=E('div','m-modal');add(m,add(E('div','h'),E('span','','Create Folder'),I('x')));
  add(m,A('fname',add(E('div','m-fl'),E('label','','Folder Name'),E('div','v','Enter folder name'))));
  add(m,A('fbg',add(E('div','m-fl m-flr'),E('label','','Folder Background Image'),E('span','','Upload'))),E('div','m-hr'));
  add(m,A('fprompt',add(E('div','m-fl'),E('label','','System Prompt'),E('div','v',L('Ex. : Tu es un tuteur de statistiques. Réponds avec des exemples simples.','e.g. You are a statistics tutor. Answer with simple examples.')))));
  add(m,A('fknow',add(E('div','m-fl'),add(E('div','m-kn'),E('span','','Knowledge'),E('span','','Select Knowledge'),E('span','','Upload')),E('div','m-knnote','To attach knowledge base here, add them to the "Knowledge" workspace first.'))));
  add(m,add(E('div','m-fbf'),E('span'),A('fsave',E('span','m-save','Save'))));return m;}

function channelMock(){var g=E('div','m-chan'),s=E('div','m-side');
  add(s,add(E('div','m-sh1'),E('span','m-oi','OI'),E('span','',PRODUCT)));
  add(s,A('cplus',add(E('div','m-sg'),E('span','','Channels'),add(E('span','m-end'),I('plus')))));
  add(s,A('clist',add(E('div','m-si'),I('hash'),E('span','',L('projet-alpha','project-alpha')))),add(E('div','m-si'),I('hash'),E('span','',L('annonces','announcements'))));
  var mn=E('div','m-cmain');add(mn,add(E('div','m-chead'),add(E('span'),'# ',L('projet-alpha','project-alpha')),A('cpin',add(E('span','m-ib'),I('pin')))));
  var m1=add(E('div','m-msg'),E('span','m-av','AL'),add(E('div'),add(E('div','n'),'Alice',E('small','','10:02')),E('div','x',L('J’ai déposé le plan du rapport, vos avis ?','I shared the report outline, thoughts?')),A('creact',add(E('div','m-under'),add(E('span','m-pill'),'👍 3'),add(E('span','m-pill'),I('smile'))))));
  var m2=add(E('div','m-msg'),E('span','m-av','YB'),add(E('div'),add(E('div','n'),'Yanis',E('small','','10:05')),A('cmention',add(E('div','x'),E('span','m-mention','@'+PRODUCT),L(' résume les décisions de ce fil en 3 points.',' summarize the decisions in this thread in 3 points.'))),A('cthread',add(E('div','m-under'),add(E('span','m-pill'),I('reply'),E('span','',L('2 réponses','2 replies')))))));
  add(mn,m1,m2,A('cinput',add(E('div','m-cin'),I('plus'),E('span','m-grow','Send a Message'),I('arrowr'))));
  add(g,s,mn);return g;}

function doDont(){var c=E('div','m-cols'),a=E('div','m-card'),b=E('div','m-card');
  add(a,E('h3','',L('À faire','Do')));[L('Vérifier chiffres, dates et citations','Check figures, dates and quotes'),L('Ouvrir les sources affichées','Open the cited sources'),L('Préciser votre demande si besoin','Refine your request when needed'),L('Donner votre avis sur les réponses','Rate the answers')].forEach(function(t){add(a,add(E('div','m-rule ok'),I('okc'),E('span','',t)));});
  add(b,E('h3','',L('À éviter','Avoid')));[L('Mots de passe, codes, identifiants','Passwords, codes, credentials'),L('Données personnelles de tiers','Other people’s personal data'),L('Documents confidentiels non autorisés','Unauthorized confidential documents'),L('Copier une réponse sans la relire','Copying an answer without reading it')].forEach(function(t){add(b,add(E('div','m-rule no'),I('noc'),E('span','',t)));});
  add(c,a,b);return c;}

function userMenu(){var m=E('div','m-user');
  var h=A('uhead',add(E('div','m-uh'),E('span','m-face',L('V','Y')),E('span','',L('Votre nom','Your name')),E('span','dot'),ADMIN?E('small','','5'):null));add(m,h);
  add(m,mItem('ustatus','smile','Update your status'),E('div','m-sep'));
  if(anyWs())add(m,mItem('uworkspace','grid','Workspace'));
  if(ft('notes'))add(m,mItem('unotes','book','Notes'));
  if(ft('calendar'))add(m,mItem('ucalendar','calendar','Calendar'));
  if(ft('automations'))add(m,mItem('uauto','clock','Automations'));
  /*__ADMIN_ONLY_START__*/
  if(ADMIN)add(m,mItem('uplay','code','Playground'));
  add(m,E('div','m-sep'));
  if(ADMIN)add(m,mItem('uadmin','user','Admin Panel'));
  /*__ADMIN_ONLY_END__*/
  add(m,mItem('usettings','gear','Settings'),mItem('usignout','logout','Sign Out'));return m;}
function calendarPage(){var g=E('div','m-calapp'),sd=E('div','m-calside'),mn=E('div','m-calmain');
  add(sd,A('newevent',add(E('div','m-newev'),I('plus'),E('span','','New Event'))));
  var mini=A('mini',E('div','m-mini'));['M','T','W','T','F','S','S'].forEach(function(d){add(mini,E('span','',d));});for(var d=1;d<=30;d++){var c=E('span','',d);if(d===16)c.className='t';add(mini,c);}add(sd,mini);
  add(sd,A('calplus',add(E('div','m-calh'),E('span','','Calendars'),I('plus'))));
  add(sd,A('personal',add(E('div','m-calrow'),add(E('span','m-sq'),null),E('span','','Personal'))));sd.lastChild.firstChild.style.background='#3B82F6';
  if(ft('automations')){add(sd,A('sched',add(E('div','m-calrow'),E('span','m-sq'),E('span','','Scheduled Tasks'))));sd.lastChild.firstChild.style.background='#8B5CF6';}
  add(mn,add(E('div','m-calbar'),A('today',E('span','m-btn2','Today')),A('nav',add(E('span','nav'),I('left'),I('right'))),E('h4','','September 2026'),A('view',add(E('span','view'),E('span','','Month'),I('down')))));
  var gr=E('div','m-grid7');['Mon','Tue','Wed','Thu','Fri','Sat','Sun'].forEach(function(d){add(gr,E('div','hd',d));});
  for(var i=0;i<28;i++){var day=i+1,cell=E('div','',day);
    if(day%7===1)add(cell,E('span','m-ev g',L('Point équipe 9:00','Team standup 9:00')));
    if(day===16)add(cell,A('event',E('span','m-ev b',L('Réunion projet 10:00','Project meeting 10:00'))));
    if(ft('automations')&&(day===18||day===25))add(cell,A('autoev',E('span','m-ev v',L('Résumé hebdo 8:00','Weekly digest 8:00'))));
    add(gr,cell);}
  add(mn,gr);add(g,sd,mn);return g;}
function eventForm(){var f=E('div','m-form');add(f,add(E('div','h'),E('span','','New Event'),I('x')));
  function row(id,icon,lbl,val,ph){return A(id,add(E('div','m-f'),I(icon),E('span','lbl',lbl),E('span','val'+(ph?' ph':''),val)));}
  add(f,row('etitle','edit','Title',L('Réunion de projet','Project meeting')),row('ecal','calendar','Calendar','Personal'),
    A('ewhen',add(E('div','m-f'),I('clock'),E('span','lbl','When'),E('span','val','16/09/2026  10:00 – 11:00'),E('span','',L('All day','All day')),E('span','m-sw'))),
    row('eloc','mappin','Location',L('Salle 204','Room 204')),row('erepeat','repeat','Repeat','Weekly'),row('eremind','bell','Reminder','10 minutes before'),row('edesc','notefile','Description',L('Ordre du jour…','Agenda…'),true));
  add(f,add(E('div','m-fbf'),E('span'),A('ecreate',E('span','m-save','Create'))));return f;}
function autoList(){var p=E('div','m-page');
  add(p,add(E('div','m-nh'),add(E('span','t'),'Automations',E('span','','3')),A('acreate',add(E('span','m-split'),E('span','','Create'),add(E('i'),I('down'))))));
  add(p,add(E('div','m-ns'),A('asearch',add(E('span','s'),I('search'),E('span','','Search'))),A('afilter',add(E('span','f'),add(E('span'),'All',I('down'))))));
  [[L('Résumé hebdomadaire des actualités','Weekly news digest'),L('Chaque lundi à 08:00 · prochaine : 21 sept.','Every Monday at 08:00 · next: 21 Sep'),true],[L('Rappel quotidien','Daily reminder'),L('Chaque jour à 18:00','Every day at 18:00'),true],[L('Veille concurrentielle','Market watch'),L('En pause','Paused'),false]].forEach(function(r,k){
    var row=add(E('div','m-rowx'),I('clock'),add(E('span'),r[0],E('small','',r[1])),k===0?A('aswitch',E('span','m-sw'+(r[2]?' on':''))):E('span','m-sw'+(r[2]?' on':'')));add(p,k===0?A('arow',row):row);});
  return p;}
function autoEditor(){var p=E('div','m-page');
  add(p,add(E('div','m-nh'),E('span','t',L('Résumé hebdomadaire des actualités','Weekly news digest')),add(E('div','m-tbtns'),A('arun',add(E('span','run'),I('play'),E('span','','Run now'))),A('apause',add(E('span'),I('pause'),E('span','','Pause'))),A('adelete',add(E('span'),I('trash'),E('span','','Delete'))))));
  function row(id,icon,lbl,node){return A(id,add(E('div','m-f'),I(icon),E('span','lbl',lbl),node));}
  add(p,row('atitle','edit','Title',E('span','val',L('Résumé hebdomadaire des actualités','Weekly news digest'))),
    row('ainstr','notefile','Instructions',E('span','val',L('Recherche les actualités du secteur de la semaine et résume-les en 5 points avec les liens.','Search this week’s industry news and summarize it in 5 points with links.'))),
    row('amodel','layers','Model',E('span','val',PRODUCT)));
  var seg=E('span','m-seg');['Once','Hourly','Daily','Weekly','Monthly','Custom'].forEach(function(x){add(seg,E('span',x==='Weekly'?'sel':'',x));});add(p,row('asched','repeat','Schedule',seg));
  if(ft('folders'))add(p,row('afolder','folder','Folder',E('span','val',L('Veille','Watch'))));
  var logs=A('alogs',E('div'));add(logs,E('div','m-tg','Execution logs'),add(E('div','m-log'),add(E('span','ok'),I('okc')),E('span','','success · 14 Sep 08:00'),E('a','',L('Ouvrir le chat','Open chat'))),add(E('div','m-log'),add(E('span','err'),I('noc')),E('span','','error · 7 Sep 08:00'),E('a','',L('Détails','Details'))));add(p,logs);
  return p;}
function builtinTools(){var w=E('div','m-resp');
  add(w,E('div','m-bubble',L('Rappelle-moi vendredi à 15h de relancer le prestataire.','Remind me on Friday at 3pm to follow up with the supplier.')));w.firstChild.style.cssText='margin-left:auto;width:max-content;max-width:80%;padding:8px 12px;border:1px solid var(--border);border-radius:16px;background:var(--surface);font-size:13px';
  add(w,A('toolcall',add(E('div','m-tc'),I('check'),E('span','','create_calendar_event'),I('down'))));w.lastChild.style.marginTop='12px';
  add(w,E('p','',L('C’est noté : événement créé vendredi à 15:00, rappel 10 minutes avant.','Done: event created on Friday at 15:00 with a reminder 10 minutes before.')));w.lastChild.style.cssText='margin:8px 0 0;font-size:13.5px';
  var bt=E('div','m-bt');builtinCats().forEach(function(c){add(bt,mItem(c.a,c.i,c.k));});add(w,bt);return w;}
function builtinCats(){var c=[];
  c.push({a:'bt-time',i:'clock',k:L('Date et heure','Date & time'),t:L('Connaît la date et calcule des délais : « dans 3 semaines », « lundi prochain ».','Knows the date and computes delays: “in 3 weeks”, “next Monday”.')});
  if(ft('web_search'))c.push({a:'bt-web',i:'globe',k:L('Recherche web','Web search'),t:L('Cherche et lit des pages quand Web Search est activé.','Searches and reads pages when Web Search is on.')});
  if(av('knowledge')||ws('knowledge'))c.push({a:'bt-know',i:'db',k:L('Documents','Knowledge'),t:L('Parcourt et lit les bases de documents accessibles.','Browses and reads accessible document collections.')});
  if(ft('memories'))c.push({a:'bt-mem',i:'brain',k:L('Mémoire','Memory'),t:L('Retient ou retrouve une préférence : « souviens-toi que je travaille sur le projet Alpha ».','Saves or recalls a preference: “remember I work on Project Alpha”.')});
  if(ft('notes'))c.push({a:'bt-notes',i:'book',k:'Notes',t:L('Recherche, lit, crée et met à jour vos notes.','Searches, reads, creates and updates your notes.')});
  c.push({a:'bt-chats',i:'history',k:L('Conversations','Chats'),t:L('Retrouve une ancienne conversation : « qu’avions-nous décidé sur le budget ? ».','Finds a past chat: “what did we decide about the budget?”.')});
  if(ft('channels'))c.push({a:'bt-chan',i:'hash',k:L('Canaux','Channels'),t:L('Cherche dans les canaux dont vous êtes membre.','Searches channels you belong to.')});
  if(ft('calendar'))c.push({a:'bt-cal',i:'calendar',k:L('Calendrier','Calendar'),t:L('Crée, déplace ou supprime des événements et consulte votre semaine.','Creates, moves or deletes events and checks your week.')});
  if(ft('automations'))c.push({a:'bt-auto',i:'repeat',k:L('Automatisations','Automations'),t:L('Crée, liste, met en pause ou supprime une tâche planifiée.','Creates, lists, pauses or deletes a scheduled task.')});
  if(ft('image_generation'))c.push({a:'bt-img',i:'image',k:L('Images','Images'),t:L('Génère une image à partir de la conversation.','Generates an image from the conversation.')});
  if(ft('code_interpreter'))c.push({a:'bt-code',i:'terminal',k:L('Exécution de code','Code execution'),t:L('Exécute du code pour calculer ou analyser des données.','Runs code to calculate or analyze data.')});
  c.push({a:'bt-tasks',i:'tasks',k:L('Liste de tâches','Task list'),t:L('Pour une demande en plusieurs étapes, l’assistant tient une liste de tâches et la coche au fur et à mesure.','For multi-step requests, the assistant keeps a task list and ticks items off.')});
  return c;}

/* ============ Steps ============ */
function N(a,i,k,t){return {a:a,i:i,k:k,t:t};}
function build(){
 steps=[];var CH=null;
 function S(o){o.ch=CH;steps.push(o);}
 function chapter(name,icon,d){CH=name;if(!CHD[name])CHD[name]={i:icon,d:d};}

 chapter(L('Accueil','Home'),'map',L('Présentation du guide','About this guide'));
 if(on('show_cover'))S({cover:true,title:L('Guide d’utilisation de ','How to use ')+PRODUCT,desc:L('Bienvenue ! Ce guide interactif vous apprend à utiliser la plateforme, écran par écran. Il montre uniquement les fonctions disponibles pour votre compte.','Welcome! This interactive guide teaches you how to use the platform, screen by screen. It only shows the features available on your account.'),notes:[]});
 chapter(L('Démarrer','Start'),'bolt',L('L’écran d’accueil et les suggestions','Home screen and suggestions'));
 if(on('show_welcome'))S({title:L('Bienvenue sur ','Welcome to ')+PRODUCT,desc:L('Ce guide vous montre l’interface réelle, élément par élément. Cliquez sur un élément de l’écran ou sur son explication pour le mettre en évidence.','This guide walks you through the real interface, one element at a time. Click any element on the screen or its explanation to highlight it.'),
   mock:function(){return home({});},stage:'',
   notes:[N('title','layers',L('Assistant actif','Active assistant'),L('Le nom en haut indique l’assistant qui va répondre.','The name at the top shows which assistant will answer.')),
     N('input','edit',L('Zone de saisie','Message box'),L('Écrivez votre demande ici. Entrée pour envoyer, Maj + Entrée pour aller à la ligne.','Type your request here. Enter sends, Shift + Enter adds a new line.')),
     N('sugg','bolt','Suggested',L('Des idées de questions prêtes à l’emploi. Cliquez sur l’une d’elles pour la placer dans la zone de saisie.','Ready-made question ideas. Click one to place it in the message box.'))],
   tip:L('Les suggestions changent selon l’assistant choisi : elles montrent ce qu’il sait bien faire.','Suggestions change with the selected assistant: they show what it does best.')});

 chapter(L('Zone de saisie','Message box'),'edit',L('Le bouton + et ses menus','The + button and its menus'));
 var cn=[N('plus','plus','+',L('Ajouter du contexte : fichiers, capture, page web, notes, documents, anciennes conversations.','Add context: files, capture, web page, notes, documents, past chats.')),
   N('integ','integ',L('Intégrations','Integrations'),L('Activer des outils et fonctions pour cette conversation, comme la recherche web.','Turn on tools and features for this chat, such as web search.')),
   N('model','layers',L('Sélecteur d’assistant','Assistant selector'),L('Choisir l’assistant ou le modèle qui répond.','Choose the assistant or model that answers.'))];
 if(ft('stt'))cn.push(N('mic','mic',L('Dictée','Dictation'),L('Parlez : votre voix est transcrite en texte, que vous relisez avant d’envoyer.','Speak: your voice becomes text you can review before sending.')));
 cn.push(N('voice','wave',L('Envoyer / mode vocal','Send / voice mode'),L('Quand la zone est vide, ce bouton lance le mode vocal. Dès que vous écrivez, il devient le bouton d’envoi.','When the box is empty, this button starts voice mode. Once you type, it becomes the send button.')));
 if(on('show_composer'))S({title:L('Les commandes de la zone de saisie','The message box controls'),desc:L('Tout part de cette zone. Voici à quoi sert chaque bouton.','Everything starts here. Here is what each button does.'),mock:function(){return composer({});},stage:'center',notes:cn});

 var pi=plusItems(),pn=[];
 pi.forEach(function(x){var t={
   toolperm:L('Définit si l’assistant peut utiliser les outils librement (Full access) ou doit vous demander avant chaque action (Ask for approval). La flèche ouvre ce choix.','Sets whether the assistant can use tools freely (Full access) or must ask before each action (Ask for approval). The arrow opens this choice.'),
   upload:L('Envoyer un fichier depuis votre ordinateur : PDF, Word, Excel, image. Vous pouvez aussi le glisser dans la zone de saisie.','Send a file from your computer: PDF, Word, Excel, image. You can also drag it into the message box.'),
   capture:L('Prendre une capture d’écran d’une fenêtre ou de l’écran et la joindre au message.','Take a screenshot of a window or your screen and attach it.'),
   webpage:L('Coller l’adresse d’une page web : son texte est lu et ajouté comme contexte.','Paste a web page address: its text is read and added as context.'),
   attfiles:L('La flèche ouvre la liste des fichiers que vous avez déjà envoyés, pour les réutiliser sans les téléverser à nouveau.','The arrow opens files you already uploaded, so you can reuse them without uploading again.'),
   attnotes:L('La flèche liste vos Notes. Le contenu complet de la note est ajouté au message.','The arrow lists your Notes. The full note content is added to the message.'),
   attknow:L('La flèche liste les bases de documents auxquelles vous avez accès. L’assistant cherche la réponse dedans.','The arrow lists document collections you can access. The assistant searches them for the answer.'),
   refchats:L('La flèche liste vos anciennes conversations pour vous appuyer sur un échange précédent.','The arrow lists your past chats so you can build on an earlier exchange.')}[x.id];
   pn.push(N(x.id,x.i,x.l+(x.arrow?'  ›':''),t));});
 if(on('show_plus_menu'))S({title:L('Le bouton + : ajouter du contexte','The + button: add context'),desc:L('Cliquez sur + pour ouvrir ce menu. Les lignes avec une flèche › ouvrent une seconde liste : cliquez dessus ici pour la voir.','Click + to open this menu. Rows with an arrow › open a second list: click them here to see it.'),
   stage:'tall',notes:pn,
   mock:function(){var c=composer({menu:plusMenu()});var sub=active&&subFor(active==='perm-full'||active==='perm-ask'?'toolperm':active);if(sub)add(c,sub);return c;},
   tip:L('Plus le contexte est précis, meilleure est la réponse. N’ajoutez que ce qui est utile à la question.','Precise context gives better answers. Only add what the question needs.')});

 if(hasTools&&on('show_tool_permissions'))S({title:L('Permissions des outils','Tool permissions'),desc:L('Dans + › Tool Permissions, décidez du niveau de contrôle quand l’assistant utilise un outil.','In + › Tool Permissions, decide how much control you keep when the assistant uses a tool.'),
   stage:'tall',start:'perm-full',
   mock:function(){var c=composer({menu:plusMenu()});add(c,subFor('toolperm'));return c;},
   notes:[N('perm-full','check','Full access',L('L’assistant lance les outils dont il a besoin sans vous interrompre. Idéal pour les recherches et lectures.','The assistant runs the tools it needs without interrupting you. Best for searches and reading.')),
     N('perm-ask','shield','Ask for approval',L('Avant chaque action, l’assistant affiche ce qu’il veut faire et attend votre accord. Recommandé pour tout ce qui envoie, modifie ou supprime.','Before each action, the assistant shows what it wants to do and waits for your approval. Recommended for anything that sends, changes or deletes.'))],
   warn:L('En cas de doute, choisissez Ask for approval : vous gardez la main sur chaque action.','If in doubt, choose Ask for approval: you stay in control of every action.')});

 chapter(L('Intégrations','Integrations'),'integ',L('Outils, recherche web, fonctions','Tools, web search, features'));
 var ii=integItems(),inn=[];
 ii.forEach(function(x){var t;
   if(x.id==='tools')t=L('La flèche › ouvre la liste de vos outils. Activez uniquement ceux utiles à la conversation en cours.','The arrow › opens your tools. Turn on only the ones this conversation needs.');
   else if(x.id==='web')t=L('Cherche sur Internet et cite ses sources. À activer pour toute information récente : actualités, dates, règlements.','Searches the internet and cites sources. Turn on for anything recent: news, dates, regulations.');
   else if(x.id==='code')t=L('Exécute du code pour calculer, analyser un fichier de données ou produire un graphique.','Runs code to calculate, analyze a data file or build a chart.');
   else if(x.id==='imagegen')t=L('Crée une image à partir de votre description.','Creates an image from your description.');
   else t=(x.desc||L('Fonction ajoutée par votre organisation. L’interrupteur l’active pour cette conversation.','A feature added by your organization. The switch turns it on for this chat.'))+' '+L('L’icône réglages permet d’ajuster ses options personnelles.','The settings icon adjusts its personal options.');
   inn.push(N(x.id,x.i,x.l+(x.arrow?'  ›':''),t));});
 if(inn.length&&on('show_integrations'))S({title:L('Le bouton Intégrations','The Integrations button'),desc:L('Le bouton à côté de + regroupe les outils et fonctions. Chaque interrupteur s’applique à la conversation en cours.','The button next to + groups tools and features. Each switch applies to the current conversation.'),
   stage:'tall',notes:inn,mock:function(){var c=composer({menu:integMenu()});if(active==='tools'||active==='tool-row')add(c,toolsSub());return c;},
   tip:L('N’activez que ce dont vous avez besoin : trop d’outils actifs ralentit et disperse les réponses.','Only turn on what you need: too many active tools slows and scatters answers.')});
 if(hasTools&&on('show_tools'))S({title:L('Utiliser un outil','Using a tool'),desc:L('Intégrations › Tools › ouvre la liste. Activez l’outil, puis demandez simplement ce que vous voulez : l’assistant l’utilisera au bon moment.','Integrations › Tools › opens the list. Turn the tool on, then just ask: the assistant uses it when needed.'),
   stage:'tall',start:'tool-row',mock:function(){var c=composer({menu:integMenu()});add(c,toolsSub());return c;},
   notes:[N('tool-row','wrench',L('Interrupteur d’outil','Tool switch'),L('Activé pour cette conversation seulement. Désactivez-le quand vous n’en avez plus besoin.','On for this conversation only. Turn it off when you no longer need it.')),
     N('tools','left',L('Retour','Back'),L('La flèche à gauche revient au menu Intégrations.','The left arrow returns to the Integrations menu.'))]});
 if(ft('web_search')&&on('show_web_search'))S({title:L('Recherche web','Web search'),desc:L('Intégrations › Web Search. Posez ensuite votre question : la réponse affiche les sources consultées.','Integrations › Web Search. Then ask your question: the answer shows the sources it used.'),
   stage:'tall',start:'web',mock:function(){return composer({text:L('Quelles sont les règles en vigueur en 2026 ? Cite la source officielle.','What rules apply in 2026? Cite the official source.'),menu:integMenu()});},
   notes:[N('web','globe','Web Search',L('Activez-le avant d’envoyer. Il reste actif pour la conversation.','Turn it on before sending. It stays on for the conversation.')),N('input','edit',L('Votre question','Your question'),L('Précisez la période, le pays et le type de source souhaité.','Specify the period, country and type of source you want.'))],
   example:L('Trouve les informations officielles les plus récentes sur ce sujet et donne le lien de chaque source.','Find the latest official information on this topic and give the link to each source.'),
   warn:L('Sans recherche web, l’assistant répond avec des connaissances qui peuvent être anciennes.','Without web search, the assistant answers from knowledge that may be outdated.')});

 chapter(L('Assistants','Assistants'),'layers',L('Choisir le bon assistant','Choosing the right assistant'));
 if(on('show_models'))S({title:L('Choisir l’assistant','Choosing the assistant'),desc:L('Cliquez sur le nom à droite de la zone de saisie pour ouvrir la liste.','Click the name on the right of the message box to open the list.'),stage:'tall',start:'minfo',
   mock:function(){var c=composer({}),m=E('div','m-menu right');m.style.minWidth='300px';
     add(m,A('msearch',add(E('div','m-subsearch'),I('search'),E('span','m-grow','Search a model'),E('span','','All'),I('down'))));
     add(m,A('minfo',mItem(null,'layers',PRODUCT,[I('info'),I('check')])),mItem(null,'layers',L('Modèle généraliste','General model')),mItem(null,'layers',L('Modèle raisonnement','Reasoning model')));
     add(m,A('mdefault',add(E('div','m-it'),add(E('span','m-end'),E('span','','Set as default')))));add(c,m);return c;},
   notes:[N('msearch','search','Search a model',L('Tapez un nom pour filtrer. « All » filtre par catégorie.','Type a name to filter. “All” filters by category.')),
     N('minfo','info',L('Icône d’information','Info icon'),L('Survolez-la pour lire le rôle de l’assistant avant de le choisir.','Hover it to read what the assistant is for before choosing it.')),
     N('mdefault','check','Set as default',L('Utiliser cet assistant par défaut pour vos nouvelles conversations.','Use this assistant by default for new chats.'))],
   tip:L('Un assistant spécialisé répond à partir de ses documents. Pour résumer vos fichiers ou chercher sur Internet, prenez un modèle généraliste.','A specialized assistant answers from its own documents. To summarize your files or search the web, pick a general model.')});
 if(on('show_suggestions'))S({title:L('Les questions suggérées','Suggested questions'),desc:L('Sous la zone de saisie d’une nouvelle conversation, des suggestions montrent des usages typiques de l’assistant.','Below the message box of a new chat, suggestions show typical uses of the assistant.'),start:'sugg',
   mock:function(){return home({});},notes:[N('sugg','bolt','Suggested',L('Cliquez sur une suggestion : elle remplit la zone de saisie. Complétez-la avec vos détails avant d’envoyer.','Click a suggestion: it fills the message box. Add your details before sending.')),N('title','layers',L('Liées à l’assistant','Tied to the assistant'),L('Changez d’assistant pour voir d’autres suggestions.','Switch assistants to see other suggestions.'))]});

 chapter(L('Réponses','Answers'),'refresh',L('Les actions sous chaque réponse','Actions under each answer'));
 var an=[N('edit','pencil',L('Modifier','Edit'),L('Corrige le texte de la réponse, par exemple avant de la copier.','Edit the answer text, for example before copying it.')),
   N('copy','clipboard',L('Copier','Copy'),L('Copie la réponse avec sa mise en forme.','Copies the answer with its formatting.')),
   N('speak','speaker',L('Lire à voix haute','Read aloud'),L('Écoutez la réponse.','Listen to the answer.')),
   N('ginfo','info',L('Informations','Info'),L('Détails de génération : durée, longueur.','Generation details: time, length.')),
   N('good','tup',L('Bonne réponse','Good response'),L('Ouvre le formulaire d’avis positif. Voir chapitre Avis.','Opens the positive rating form. See the Feedback chapter.')),
   N('bad','tdown',L('Mauvaise réponse','Bad response'),L('Ouvre le formulaire pour signaler un problème.','Opens the form to report a problem.')),
   N('continue','play',L('Continuer','Continue'),L('Si la réponse s’arrête en cours de route, l’assistant reprend là où il s’était arrêté.','If the answer stops midway, the assistant picks up where it left off.')),
   N('regen','refresh',L('Régénérer','Regenerate'),L('Produit une nouvelle version. Vous pouvez naviguer entre les versions.','Produces a new version. You can switch between versions.'))];
 ACTIONS.slice(0,4).forEach(function(a,k){an.push(N('act-'+k,k%2?'spark':'doc',a.name,(a.description||L('Action ajoutée par votre organisation.','Action added by your organization.'))+' '+L('Cliquez pour l’appliquer à cette réponse.','Click to apply it to this answer.')));});
 if(on('show_response_actions'))S({title:L('Les actions sous chaque réponse','Actions under each answer'),desc:L('Survolez une réponse : cette barre apparaît. Chaque icône a un rôle précis.','Hover an answer: this bar appears. Each icon has a specific role.'),stage:'center',
   mock:function(){var r=E('div','m-resp'),ls=E('div','m-lines');['100%','92%','70%'].forEach(function(w){var l=E('div','m-l');l.style.width=w;add(ls,l);});add(r,ls,actionsRow());return r;},
   notes:an,info:ACTIONS.length?L('Les dernières icônes sont des actions propres à votre compte. Survolez-les pour voir leur nom.','The last icons are actions specific to your account. Hover them to see their name.'):null});

 chapter(L('Avis','Feedback'),'tup',L('Noter les bonnes et mauvaises réponses','Rating good and bad answers'));
 if(on('show_feedback'))S({title:L('Donner un avis positif','Giving positive feedback'),desc:L('👍 ouvre ce formulaire. Vos avis aident votre organisation à choisir et améliorer les assistants.','👍 opens this form. Your ratings help your organization choose and improve assistants.'),stage:'tall',start:'score',
   mock:function(){var r=E('div','m-resp');add(r,actionsRow('good'),feedback('good'));return r;},
   notes:[N('score','tup',L('Note de 6 à 10','Score 6 to 10'),L('Après 👍, seules les notes hautes sont disponibles. 10 = parfaite.','After 👍, only high scores are available. 10 = perfect.')),
     N('reasons','check','Why?',L('Choisissez ce qui était vraiment bien : exactitude, respect des consignes, clarté…','Pick what was actually good: accuracy, following instructions, clarity…')),
     N('details','edit',L('Détails','Details'),L('Une phrase suffit pour dire ce qui vous a aidé.','One sentence is enough to say what helped.')),
     N('tags','hash',L('Étiquettes','Tags'),L('Classez l’avis par sujet, par exemple « recherche » ou « rédaction ».','Categorize by topic, e.g. “search” or “writing”.')),
     N('save','check','Save',L('Enregistre l’avis. Il n’est pas visible par les autres utilisateurs.','Saves the rating. It is not visible to other users.'))]});
 if(on('show_feedback'))S({title:L('Signaler une mauvaise réponse','Reporting a bad answer'),desc:L('👎 ouvre la version négative. Un avis précis est bien plus utile qu’une simple note.','👎 opens the negative version. A precise rating is far more useful than a score alone.'),stage:'tall',start:'reasons',
   mock:function(){var r=E('div','m-resp');add(r,actionsRow('bad'),feedback('bad'));return r;},
   notes:[N('score','tdown',L('Note de 1 à 5','Score 1 to 5'),L('1 = inutilisable, 5 = moyen.','1 = unusable, 5 = average.')),
     N('reasons','warn','Why?',L('Not factually correct : erreur de fait. Didn’t fully follow instructions : consigne ignorée. Refused when it shouldn’t have : refus injustifié. Too verbose : trop long.','Not factually correct: wrong facts. Didn’t fully follow instructions: ignored a request. Refused when it shouldn’t have: unjustified refusal. Too verbose: too long.')),
     N('details','edit',L('Détails','Details'),L('Indiquez ce qui était faux et, si possible, la bonne information.','Say what was wrong and, if you can, the correct information.')),
     N('save','check','Save',L('Enregistrez, puis reformulez ou régénérez pour obtenir une meilleure réponse.','Save, then rephrase or regenerate for a better answer.'))],
   compare:[L('« Nul. »','“Bad.”'),L('« La date limite indiquée est le 15 mars, mais le site officiel indique le 31 mars. »','“It says the deadline is 15 March, but the official site says 31 March.”')]});

 chapter(L('Barre latérale','Sidebar'),'panel',L('Naviguer dans vos espaces','Moving between spaces'));
 var sn=[N('collapse','panel',L('Masquer la barre','Hide sidebar'),L('Replie la barre pour gagner de la place. Cliquez à nouveau pour la rouvrir.','Collapses the sidebar for more room. Click again to reopen.')),
   N('new','edit','New Chat',L('Démarre une conversation vide. Changez de conversation quand vous changez de sujet.','Starts an empty chat. Start a new one when you change topic.')),
   N('search','search','Search',L('Retrouve une conversation par mot-clé.','Finds a chat by keyword.'))];
 if(ft('notes'))sn.push(N('notes','book','Notes',L('Vos documents personnels, avec un assistant intégré. Voir chapitre Notes.','Your personal documents with a built-in assistant. See the Notes chapter.')));
 if(anyWs())sn.push(N('workspace','grid','Workspace',L('Créer et gérer des assistants, documents ou modèles de demandes, selon vos droits.','Create and manage assistants, documents or prompt templates, depending on your rights.')));
 sn.push(N('models','layers','Models',L('Vos assistants épinglés, pour les ouvrir en un clic.','Your pinned assistants, one click away.')));
 if(ft('channels'))sn.push(N('channels','hash','Channels  +',L('Espaces de discussion partagés. Le + crée un canal si vous en avez le droit.','Shared discussion spaces. The + creates a channel if you are allowed.')));
 if(ft('folders'))sn.push(N('folders','folder','Folders  +',L('Regroupez vos conversations par projet. Le + crée un dossier.','Group chats by project. The + creates a folder.')));
 sn.push(N('chats','history','Chats  ›  ⋯',L('L’historique. La flèche replie la liste ; le menu ⋯ propose d’archiver ou supprimer. Clic droit sur une conversation pour l’épingler, la renommer ou la déplacer.','Your history. The arrow collapses the list; ⋯ offers archive or delete. Right-click a chat to pin, rename or move it.')));
 if(on('show_sidebar_navigation'))S({title:L('La barre latérale','The sidebar'),desc:L('À gauche, tout pour naviguer entre vos conversations et espaces.','On the left, everything to move between chats and spaces.'),stage:'flush center',
   mock:function(){var a=E('div','m-app');add(a,sidebar(true),E('div','m-mainpane',L('Conversation','Conversation')));return a;},notes:sn});

 chapter(L('Menu utilisateur','User menu'),'user',L('Votre nom en bas à gauche','Your name, bottom left'));
 var un=[N('uhead','user',L('Votre compte','Your account'),L('Cliquez sur votre nom en bas de la barre latérale pour ouvrir ce menu. Le point vert indique que vous êtes en ligne.','Click your name at the bottom of the sidebar to open this menu. The green dot means you are online.')+(ADMIN?L(' Le nombre, visible par les administrateurs, indique les utilisateurs actifs.',' The number, shown to administrators, is the count of active users.'):'')),
   N('ustatus','smile','Update your status',L('Ajoutez un emoji et un message court (« en réunion », « en cours »), visible par les autres dans les canaux.','Set an emoji and a short message (“in a meeting”, “in class”), shown to others in channels.'))];
 if(anyWs())un.push(N('uworkspace','grid','Workspace',L('Créer et gérer assistants, bases de documents, modèles de demandes, selon vos droits.','Create and manage assistants, document collections and prompt templates, depending on your rights.')));
 if(ft('notes'))un.push(N('unotes','book','Notes',L('Ouvre vos Notes.','Opens your Notes.')));
 if(ft('calendar'))un.push(N('ucalendar','calendar','Calendar',L('Votre agenda personnel, avec rappels. Voir chapitre Calendrier.','Your personal calendar with reminders. See the Calendar chapter.')));
 if(ft('automations'))un.push(N('uauto','clock','Automations',L('Des demandes qui s’exécutent toutes seules à heure fixe. Voir chapitre Automatisations.','Requests that run on their own on a schedule. See the Automations chapter.')));
 /*__ADMIN_ONLY_START__*/
 if(ADMIN)un.push(N('uplay','code','Playground',L('Réservé aux administrateurs : tester un modèle avec ses paramètres bruts, hors conversation.','Admins only: test a model with raw parameters, outside a chat.')));
 if(ADMIN)un.push(N('uadmin','user','Admin Panel',L('Réservé aux administrateurs : utilisateurs, groupes et permissions, réglages, évaluations.','Admins only: users, groups and permissions, settings, evaluations.')));
 /*__ADMIN_ONLY_END__*/
 un.push(N('usettings','gear','Settings',L('Langue, thème, notifications, voix, personnalisation et mémoire.','Language, theme, notifications, voice, personalization and memory.')),N('usignout','logout','Sign Out',L('Déconnexion. Indispensable sur un ordinateur partagé.','Signs you out. Essential on a shared computer.')));
 if(on('show_user_menu'))S({title:L('Le menu utilisateur','The user menu'),desc:L('Il donne accès à vos espaces personnels et à vos réglages. Son contenu dépend de votre rôle.','It opens your personal spaces and settings. What you see depends on your role.'),stage:'center',start:'uhead',mock:userMenu,notes:un,
   tip:(ft('calendar')||ft('automations'))?L('Astuce : maintenez Maj (Shift) dans ce menu pour épingler Calendar ou Automations dans la barre latérale.','Tip: hold Shift in this menu to pin Calendar or Automations to the sidebar.'):null});

 if(on('show_notes')&&ft('notes')){
  chapter('Notes','book',L('Rédiger avec l’assistant','Writing with the assistant'));
  S({title:L('La page Notes','The Notes page'),desc:L('Barre latérale › Notes. Un espace pour rédiger des textes qui durent : comptes rendus, plans, idées.','Sidebar › Notes. A place for lasting writing: minutes, outlines, ideas.'),stage:'center',
    mock:notesList,notes:[N('count','book','Notes 5',L('Nombre de notes accessibles.','Number of notes you can access.')),N('create','plus','Create  ⌄',L('Crée une note. La flèche propose d’autres options de création.','Creates a note. The arrow offers other creation options.')),
      N('nsearch','search','Search Notes',L('Recherche dans les titres et le contenu.','Searches titles and content.')),N('filters','sliders','All · Write · List',L('Filtrer (toutes, les vôtres…), le mode d’ouverture et l’affichage liste ou grille.','Filter (all, yours…), open mode, and list or grid view.')),
      N('sort','down','Title · Updated at',L('Cliquez sur une colonne pour trier.','Click a column to sort.')),N('row','dots',L('Une note','A note'),L('Cliquez pour ouvrir. ⋯ pour les options.','Click to open. ⋯ for options.'))]});
  S({title:L('L’éditeur de note','The note editor'),desc:L('Rédigez à gauche, travaillez avec l’assistant à droite.','Write on the left, work with the assistant on the right.'),stage:'flush center',
    mock:function(){return noteEditor(false);},notes:[N('ntitle','edit',L('Titre','Title'),L('Cliquez pour renommer la note.','Click to rename the note.')),N('nmeta','info',L('Compteur','Counter'),L('Date de modification, nombre de mots et de caractères.','Last edit, word and character count.')),
      N('nbody','notefile','Write something...',L('Écrivez librement. Sélectionnez un passage pour le faire réécrire par l’assistant.','Write freely. Select a passage to have the assistant rewrite it.')),N('undo','undo',L('Annuler / rétablir','Undo / redo'),L('Revenez sur une modification, y compris celles faites par l’assistant.','Undo any change, including those made by the assistant.')),
      N('nchat','bubble','Chat',L('Ouvre ou ferme le panneau assistant de la note.','Opens or closes the note’s assistant panel.')),N('nrec','mic',L('Enregistrer','Record'),L('Dictez ou enregistrez de l’audio : le texte est ajouté à la note.','Dictate or record audio: the text is added to the note.')),
      N('nmore','dots','⋯',L('Télécharger, partager, épingler, supprimer. Voir étape suivante.','Download, share, pin, delete. See next step.')),N('access','lock','Access',L('Qui peut voir ou modifier la note. Par défaut elle est privée.','Who can view or edit the note. Private by default.')),
      N('nprompts','bolt','Suggested prompts',L('Améliorer, résumer, extraire les actions, réécrire la sélection : l’assistant modifie la note directement.','Enhance, summarize, extract action items, rewrite selection: the assistant edits the note directly.')),N('ncomposer','edit','Send a Message',L('Posez votre propre demande sur la note.','Ask your own request about the note.'))],
    example:L('Transforme cette note en compte rendu : décisions, responsables, échéances.','Turn this note into minutes: decisions, owners, deadlines.')});
  S({title:L('Le menu ⋯ d’une note','The note ⋯ menu'),desc:L('Toutes les actions sur la note.','All actions for the note.'),stage:'flush center',start:'m-pin',
    mock:function(){return noteEditor(true);},notes:[N('m-download','download','Download',L('Exporter la note (texte, Markdown ou PDF).','Export the note (text, Markdown or PDF).')),N('m-upload','upcloud','Upload files',L('Joindre des fichiers à la note comme contexte pour l’assistant.','Attach files to the note as context for the assistant.')),
      N('m-share','share','Share',L('Copier un lien ou partager la note.','Copy a link or share the note.')),N('m-pin','pin','Pin to Sidebar',L('Affiche la note dans la barre latérale pour la retrouver vite et la glisser dans une conversation.','Shows the note in the sidebar to find it fast and drag it into a chat.')),
      N('m-delete','trash','Delete',L('Supprime définitivement la note.','Permanently deletes the note.'))],
    info:L('Pour utiliser une note dans une conversation : + › Attach Notes.','To use a note in a chat: + › Attach Notes.')});
 }

 if(on('show_folders')&&ft('folders')){
  chapter(L('Dossiers','Folders'),'folder',L('Organiser vos projets','Organizing your projects'));
  S({title:L('Pourquoi utiliser des dossiers','Why use folders'),desc:L('Un dossier est un projet : il range vos conversations ET donne à l’assistant les mêmes consignes et documents pour chaque conversation qu’il contient.','A folder is a project: it groups your chats AND gives the assistant the same instructions and documents for every chat inside.'),stage:'flush center',start:'folders',
    mock:function(){var a=E('div','m-app');add(a,sidebar(false),E('div','m-mainpane',''));return a;},
    notes:[N('folders','folder','Folders  +',L('Cliquez sur + pour créer un dossier. Glissez-déposez ensuite des conversations dedans.','Click + to create a folder. Then drag chats into it.')),N('chats','history',L('Nouvelle conversation dans un dossier','New chat in a folder'),L('Ouvrez le dossier puis New Chat : la conversation hérite de ses réglages.','Open the folder then New Chat: the chat inherits its settings.'))],
    compare:null,info:L('Exemples : « Projet Alpha » avec ses documents, « Rapport annuel » avec le guide de rédaction, « Candidatures » avec votre CV.','Examples: “Project Alpha” with its documents, “Annual report” with the writing guide, “Applications” with your CV.')});
  S({title:L('Créer un dossier pas à pas','Create a folder step by step'),desc:L('Folders › + ouvre cette fenêtre.','Folders › + opens this window.'),stage:'center',start:'fname',mock:folderModal,
    notes:[N('fname','edit','Folder Name',L('Un nom clair, sans donnée sensible.','A clear name, no sensitive data.')),N('fbg','imageup','Folder Background Image',L('Optionnel : une image pour reconnaître le dossier.','Optional: an image to recognize the folder.')),
      N('fprompt','edit','System Prompt',L('Consignes appliquées à toutes les conversations du dossier : rôle, ton, format. Visible seulement si votre compte l’autorise.','Instructions applied to every chat in the folder: role, tone, format. Only shown if your account allows it.')),
      N('fknow','db','Knowledge',L('Select Knowledge ajoute une base existante, Upload envoie vos fichiers. Ils servent de contexte à chaque conversation du dossier.','Select Knowledge adds an existing collection, Upload sends your files. They become context for every chat in the folder.')),
      N('fsave','check','Save',L('Crée le dossier. Vous pourrez modifier ces réglages plus tard.','Creates the folder. You can change these settings later.'))],
    example:L('Tu es mon tuteur en statistiques. Explique avec des exemples concrets, vérifie mes calculs et pose-moi une question pour valider ma compréhension.','You are my statistics tutor. Explain with concrete examples, check my calculations and ask me a question to confirm I understood.'),exampleLabel:L('Exemple de System Prompt','Example System Prompt')});
 }

 if(on('show_channels')&&ft('channels')){
  chapter(L('Canaux','Channels'),'hash',L('Travailler à plusieurs','Working together'));
  S({title:L('Les canaux : travailler à plusieurs avec l’IA','Channels: working together with AI'),desc:L('Un canal est un espace partagé en temps réel. L’IA n’intervient que si vous la mentionnez avec @.','A channel is a real-time shared space. AI only joins in when you mention it with @.'),stage:'flush center',start:'cmention',mock:channelMock,
    notes:[N('cplus','plus','Channels  +',L('Créer un canal (selon vos droits) et choisir qui y a accès.','Create a channel (if allowed) and choose who can access it.')),N('clist','hash',L('Liste des canaux','Channel list'),L('Les canaux dont vous êtes membre. Un point signale les messages non lus.','Channels you belong to. A dot marks unread messages.')),
      N('cmention','users','@'+PRODUCT,L('Tapez @ puis choisissez un assistant : il répond dans un fil sous votre message, sans encombrer le canal.','Type @ then pick an assistant: it replies in a thread under your message, keeping the channel tidy.')),
      N('cthread','reply',L('Fils de discussion','Threads'),L('Répondez à un message précis pour garder les sujets séparés.','Reply to a specific message to keep topics separate.')),
      N('creact','smile',L('Réactions','Reactions'),L('Un emoji pour approuver sans ajouter de message.','An emoji to agree without adding a message.')),N('cpin','pin',L('Messages épinglés','Pinned messages'),L('Gardez les décisions et liens importants en haut.','Keep key decisions and links at the top.')),
      N('cinput','edit','Send a Message',L('Écrivez, joignez un fichier avec +, mentionnez une personne ou un assistant.','Write, attach a file with +, mention a person or an assistant.'))],
    warn:L('Tous les membres du canal lisent ce que vous publiez. Vérifiez qui y a accès avant de partager un document.','Every channel member reads what you post. Check who has access before sharing a document.')});
 }

 if(on('show_calendar')&&ft('calendar')){
  chapter(L('Calendrier','Calendar'),'calendar',L('Événements, rappels, IA','Events, reminders, AI'));
  var cn2=[N('newevent','plus','New Event',L('Crée un événement. Vous pouvez aussi cliquer directement sur un jour de la grille.','Creates an event. You can also click a day on the grid.')),
    N('mini','calendar',L('Mini calendrier','Mini calendar'),L('Aller rapidement à une date.','Jump quickly to a date.')),
    N('calplus','plus','Calendars  +',L('Créez d’autres calendriers (« Équipe », « Projet ») avec leur couleur.','Create more calendars (“Team”, “Project”) with their own colour.')),
    N('personal','calendar','Personal',L('Créé automatiquement : votre calendrier par défaut. Cliquez pour l’afficher ou le masquer.','Created automatically: your default calendar. Click to show or hide it.'))];
  if(ft('automations'))cn2.push(N('sched','clock','Scheduled Tasks',L('Affiche vos automatisations prévues et passées. En lecture seule : cliquez un événement pour ouvrir l’automatisation ou le chat produit.','Shows your planned and past automations. Read-only: click an event to open the automation or the chat it produced.')));
  cn2.push(N('today','calendar','Today',L('Revient à aujourd’hui. Les flèches passent à la période précédente ou suivante.','Returns to today. Arrows move to the previous or next period.')),N('view','down','Month / Week / Day',L('Change la vue : mois, semaine ou jour.','Switch view: month, week or day.')),
    N('event','calendar',L('Un événement','An event'),L('Cliquez pour le modifier ou le supprimer.','Click to edit or delete it.')));
  if(ft('automations'))cn2.push(N('autoev','repeat',L('Exécution automatique','Automation run'),L('En violet : une automatisation planifiée.','In purple: a scheduled automation.')));
  S({title:L('Le Calendrier','The Calendar'),desc:L('Menu utilisateur › Calendar. Un agenda personnel avec rappels, que l’assistant peut aussi gérer pour vous.','User menu › Calendar. A personal calendar with reminders that the assistant can also manage for you.'),stage:'flush center',start:'newevent',mock:calendarPage,notes:cn2,
    info:L('Le calendrier ne se synchronise pas avec Outlook ou Google Agenda.','The calendar does not sync with Outlook or Google Calendar.')});
  S({title:L('Créer un événement','Creating an event'),desc:L('New Event ouvre ce formulaire, prérempli à la date du jour.','New Event opens this form, prefilled with today’s date.'),stage:'center',start:'etitle',mock:eventForm,
    notes:[N('etitle','edit','Title',L('Obligatoire. Un nom clair.','Required. A clear name.')),N('ecal','calendar','Calendar',L('Dans quel calendrier ranger l’événement.','Which calendar to put it in.')),
      N('ewhen','clock','When · All day',L('Date et heures, ou journée entière.','Date and times, or all day.')),N('eloc','mappin','Location',L('Optionnel : salle ou lien de visio.','Optional: room or video link.')),
      N('erepeat','repeat','Repeat',L('No Repeat, Daily, Monday – Friday, Weekly, Monthly ou Yearly. Pas plus souvent qu’une fois par jour : pour cela, utilisez une automatisation.','No Repeat, Daily, Monday – Friday, Weekly, Monthly or Yearly. No more than once a day: use an automation for that.')),
      N('eremind','bell','Reminder',L('Alerte avant l’événement (10 minutes par défaut) : notification dans la plateforme et dans le navigateur si vous l’avez autorisé.','Alert before the event (10 minutes by default): in-app notification, and browser notification if allowed.')),
      N('edesc','notefile','Description',L('Ordre du jour, liens, documents à préparer.','Agenda, links, documents to prepare.')),N('ecreate','check','Create',L('Enregistre l’événement.','Saves the event.'))]});
 }
 if(on('show_automations')&&ft('automations')){
  chapter(L('Automatisations','Automations'),'clock',L('Demandes planifiées','Scheduled requests'));
  S({title:L('Les automatisations','Automations'),desc:L('Menu utilisateur › Automations. Une automatisation envoie une demande à heure fixe : chaque exécution crée une conversation avec la réponse.','User menu › Automations. An automation sends a request on a schedule: each run creates a chat with the answer.'),stage:'center',start:'acreate',mock:autoList,
    notes:[N('acreate','plus','Create  ⌄',L('New Automation. La flèche propose Import JSON et Export JSON pour copier vos automatisations.','New Automation. The arrow offers Import JSON and Export JSON to copy your automations.')),
      N('asearch','search','Search',L('Retrouver une automatisation par son nom.','Find an automation by name.')),N('afilter','sliders',L('Filtre de statut','Status filter'),L('Toutes, actives ou en pause.','All, active or paused.')),
      N('arow','clock',L('Une automatisation','An automation'),L('Nom, fréquence et prochaine exécution. Cliquez pour ouvrir l’éditeur.','Name, schedule and next run. Click to open the editor.')),
      N('aswitch','check',L('Interrupteur','Switch'),L('Met en pause ou relance sans supprimer.','Pauses or resumes without deleting.'))],
    info:L('Vos automatisations sont privées : personne d’autre ne peut les voir ni les lancer.','Your automations are private: nobody else can see or run them.')});
  S({title:L('Créer et suivre une automatisation','Creating and monitoring an automation'),desc:L('Remplissez les champs puis Create. L’éditeur permet ensuite de lancer, suspendre et suivre les exécutions.','Fill in the fields then Create. The editor then lets you run, pause and monitor executions.'),stage:'center',start:'ainstr',mock:autoEditor,
    notes:[N('atitle','edit','Title',L('Un nom qui dit ce que produit l’automatisation.','A name saying what it produces.')),N('ainstr','notefile','Instructions',L('La demande envoyée à chaque exécution. Soyez complet : elle doit fonctionner sans vous.','The request sent on each run. Be complete: it must work without you.')),
      N('amodel','layers','Model',L('L’assistant utilisé. Ses outils et réglages s’appliquent.','The assistant used. Its tools and settings apply.')),N('asched','repeat','Schedule',L('Once, Hourly, Daily, Weekly, Monthly ou Custom (règle avancée).','Once, Hourly, Daily, Weekly, Monthly or Custom (advanced rule).'))]
      .concat(ft('folders')?[N('afolder','folder','Folder',L('Range automatiquement les conversations produites dans un de vos dossiers.','Files the chats it creates into one of your folders.'))]:[])
      .concat([N('arun','play','Run now',L('Lance immédiatement pour tester avant d’attendre l’horaire.','Runs immediately to test before the schedule.')),N('apause','pause','Pause',L('Suspend sans perdre les réglages.','Suspends without losing settings.')),
      N('adelete','trash','Delete',L('Supprime l’automatisation et son historique.','Deletes the automation and its history.')),N('alogs','tasks','Execution logs',L('Chaque exécution avec son statut (success ou error) et le lien vers la conversation créée.','Each run with its status (success or error) and a link to the chat created.'))]),
    example:L('Chaque lundi, recherche les nouveautés publiées sur ce sujet et présente-les dans un tableau avec lien, source et date.','Every Monday, find what was published on this topic and list it in a table with link, source and date.'),exampleLabel:L('Exemple d’instructions','Example instructions'),
    tip:L('Testez toujours avec Run now. Vous pouvez aussi demander dans une conversation : « programme un résumé tous les jours à 9h ».','Always test with Run now. You can also ask in a chat: “schedule a summary every day at 9am”.')});
 }
 chapter(L('Outils intégrés','Built-in tools'),'spark',L('Ce que l’assistant fait seul','What the assistant does on its own'));
 if(on('show_builtin_tools'))S({title:L('Les outils intégrés de l’assistant','The assistant’s built-in tools'),desc:L('Selon sa configuration, l’assistant peut agir directement : consulter la date, chercher dans vos notes, créer un événement… Vous le demandez en langage naturel, il choisit l’outil.','Depending on its setup, the assistant can act directly: check the date, search your notes, create an event… You ask in plain language and it picks the tool.'),stage:'center',start:'toolcall',mock:builtinTools,
   notes:[N('toolcall','check',L('Outil utilisé','Tool used'),L('Cette ligne montre l’outil lancé. Cliquez dessus pour voir ce qui a été envoyé et reçu.','This line shows the tool that ran. Click it to see what was sent and received.'))].concat(builtinCats().map(function(c){return N(c.a,c.i,c.k,c.t);})),
   info:L('Ces outils ne fonctionnent qu’avec les assistants configurés pour eux et selon vos permissions. Si rien ne se passe, choisissez un autre assistant.','These tools only work with assistants set up for them and within your permissions. If nothing happens, choose another assistant.'),
   warn:L('Pour les actions qui modifient quelque chose, réglez + › Tool Permissions sur Ask for approval.','For actions that change something, set + › Tool Permissions to Ask for approval.')});
 /*__ADMIN_ONLY_START__*/
 if(ADMIN&&on('show_admin_section')){chapter(L('Administration','Administration'),'shield',L('Réservé aux administrateurs','Admins only'));
  S({title:L('Espace administrateur','Administrator area'),desc:L('Vous voyez ce chapitre car votre compte est administrateur.','You see this chapter because your account is an administrator.'),stage:'center',start:'uadmin',mock:userMenu,
   notes:[N('uadmin','user','Admin Panel',L('Utilisateurs, groupes et permissions (qui voit Notes, Calendar, Automations…), réglages généraux, évaluations des modèles à partir des avis.','Users, groups and permissions (who sees Notes, Calendar, Automations…), general settings, model evaluations from feedback.')),
     N('uplay','code','Playground',L('Tester un modèle en direct avec prompt système et paramètres, sans créer de conversation.','Test a model live with system prompt and parameters, without creating a chat.'))],
   warn:L('Modifiez les permissions sur un groupe de test avant de les appliquer à tous.','Change permissions on a test group before applying them to everyone.')});}
 /*__ADMIN_ONLY_END__*/
 chapter(L('Bon usage','Good practice'),'shield',L('Règles essentielles','Essential rules'));
 if(on('show_safety'))S({title:L('Utiliser l’IA de façon responsable','Using AI responsibly'),desc:L('L’assistant peut se tromper avec assurance. Vous restez responsable de ce que vous en faites.','The assistant can be confidently wrong. You stay responsible for what you do with it.'),stage:'center',mock:doDont,notes:[],
   warn:L('Respectez les règles de votre organisation sur l’usage de l’IA.','Follow your organization’s rules on using AI.')});
 S({title:L('Vous êtes prêt','You’re ready'),desc:L('Fermez le guide et posez votre première question. L’onglet Fonctions reste disponible pour revoir chaque fonctionnalité.','Close the guide and ask your first question. The Features tab stays available to revisit every feature.'),stage:'center',mock:function(){return composer({});},notes:[],links:true});

 chapters=[];steps.forEach(function(s,i){var l=chapters[chapters.length-1];if(l&&l.name===s.ch)l.end=i;else chapters.push({name:s.ch,start:i,end:i});});
}

/* ============ Library of built-in features ============ */
function libItems(){var x=[];
 var GATE={files:'show_file_upload',webpage:'show_plus_menu',refchats:'show_plus_menu',knowledge:'show_knowledge',web:'show_web_search',code:'show_code_interpreter',image:'show_image_generation',tools:'show_tools',voice:'show_voice',multi:'show_multiple_models',prompts:'show_prompts',notes:'show_notes',folders:'show_folders',channels:'show_channels',feedback:'show_feedback',memory:'show_memory',settings:'show_user_menu',status:'show_user_menu',calendar:'show_calendar',automations:'show_automations',builtin:'show_builtin_tools',playground:'show_admin_section',admin:'show_admin_section'};
 function F(g,id,i,name,short,where,what,how,example,note){if(GATE[id]&&!on(GATE[id]))return;if(!on('show_try_buttons'))example=null;x.push({g:g,id:id,i:i,name:name,short:short,where:where,what:what,how:how,example:example,note:note});}
 var G1=L('Ajouter du contexte','Add context'),G2=L('Fonctions intégrées','Built-in features'),G3=L('Organiser et collaborer','Organize and collaborate'),G4=L('Votre compte','Your account');
 if(ft('file_upload'))F(G1,'files','clip',L('Fichiers','Files'),L('PDF, Word, Excel, images','PDF, Word, Excel, images'),['+','Upload Files'],L('Analysez vos documents : résumé, extraction, traduction, comparaison.','Analyze your documents: summary, extraction, translation, comparison.'),[L('Cliquez sur + › Upload Files, ou glissez le fichier dans la zone de saisie.','Click + › Upload Files, or drag the file into the message box.'),L('Attendez la fin du chargement.','Wait for the upload to finish.'),L('Demandez ce que vous voulez en citant le fichier.','Ask what you need, referring to the file.')],L('Compare ces deux documents et liste les différences dans un tableau.','Compare these two documents and list the differences in a table.'),L('Ne joignez que des documents que vous avez le droit d’utiliser.','Only attach documents you are allowed to use.'));
 F(G1,'webpage','globe','Attach Webpage',L('Lire une page web','Read a web page'),['+','Attach Webpage'],L('Ajoute le texte d’une page à partir de son adresse.','Adds a page’s text from its address.'),[L('+ › Attach Webpage.','+ › Attach Webpage.'),L('Collez l’adresse complète (https://…).','Paste the full address (https://…).'),L('Posez votre question sur la page.','Ask about the page.')],L('Résume cette page en 5 points pour quelqu’un qui découvre le sujet.','Summarize this page in 5 points for someone new to the topic.'));
 if(av('knowledge')||ws('knowledge'))F(G1,'knowledge','db',L('Bases de documents','Knowledge'),L('Interroger des documents','Ask document collections'),['+','Attach Knowledge','›'],L('L’assistant cherche la réponse dans une collection de documents et cite les passages.','The assistant searches a document collection and cites passages.'),[L('+ › Attach Knowledge › choisissez la base. Raccourci : tapez # dans la zone de saisie.','+ › Attach Knowledge › pick the collection. Shortcut: type # in the message box.'),L('Posez une question précise.','Ask a precise question.'),L('Demandez les sources utilisées.','Ask for the sources used.')],L('Réponds uniquement à partir de ces documents et indique la section utilisée.','Answer only from these documents and name the section used.'));
 F(G1,'refchats','history','Reference Chats',L('Réutiliser une conversation','Reuse a conversation'),['+','Reference Chats','›'],L('Ajoute une conversation passée comme contexte.','Adds a past chat as context.'),[L('+ › Reference Chats › choisissez la conversation.','+ › Reference Chats › pick the chat.'),L('Demandez de continuer ou de réutiliser ce qui a été produit.','Ask to continue or reuse what was produced.')],L('À partir de la conversation jointe, rédige la version finale de l’e-mail.','Using the attached chat, write the final version of the email.'));
 if(ft('web_search'))F(G2,'web','globe','Web Search',L('Informations à jour avec sources','Up-to-date info with sources'),[L('Intégrations','Integrations'),'Web Search'],L('Recherche sur Internet et cite les pages consultées.','Searches the internet and cites the pages used.'),[L('Intégrations › activez Web Search.','Integrations › turn on Web Search.'),L('Précisez période et sources souhaitées.','Specify the period and sources you want.'),L('Ouvrez les sources pour vérifier.','Open the sources to check.')],L('Quelles sont les nouveautés officielles sur ce sujet en 2026 ? Donne les liens.','What was officially published on this topic in 2026? Give the links.'),L('Désactivez-la quand l’information récente n’est pas nécessaire.','Turn it off when recent information is not needed.'));
 if(ft('code_interpreter'))F(G2,'code','terminal','Code Interpreter',L('Calculs, données, graphiques','Calculations, data, charts'),[L('Intégrations','Integrations'),'Code Interpreter'],L('L’assistant écrit et exécute du code pour obtenir un résultat exact.','The assistant writes and runs code to get an exact result.'),[L('Intégrations › activez Code Interpreter.','Integrations › turn on Code Interpreter.'),L('Joignez un fichier CSV ou Excel si besoin.','Attach a CSV or Excel file if needed.'),L('Décrivez le calcul ou le graphique attendu.','Describe the calculation or chart you want.')],L('Calcule la moyenne et l’écart-type par groupe et trace un histogramme.','Compute mean and standard deviation per group and plot a histogram.'));
 if(ft('image_generation'))F(G2,'image','image',L('Génération d’images','Image generation'),L('Créer une illustration','Create an illustration'),[L('Intégrations','Integrations'),'Image'],L('Crée une image à partir d’une description.','Creates an image from a description.'),[L('Activez la génération d’images.','Turn on image generation.'),L('Décrivez sujet, style, cadrage et format.','Describe subject, style, framing and format.')],L('Illustration simple et moderne d’une équipe en réunion, format 16:9, sans texte.','Simple modern illustration of a team in a meeting, 16:9, no text.'));
 if(hasTools)F(G2,'tools','wrench','Tools',L('Outils de votre compte','Tools on your account'),[L('Intégrations','Integrations'),'Tools','›'],L('Des outils connectés permettent à l’assistant de consulter ou agir sur des services.','Connected tools let the assistant look up or act on services.'),[L('Intégrations › Tools › activez l’outil utile.','Integrations › Tools › turn on the tool you need.'),L('Réglez + › Tool Permissions sur Ask for approval pour valider chaque action.','Set + › Tool Permissions to Ask for approval to confirm each action.')],null,L('Ne collez jamais de mot de passe dans la conversation.','Never paste a password into the chat.'));
 if(ft('stt')||ft('call'))F(G2,'voice','mic',L('Voix','Voice'),L('Dicter, écouter, parler','Dictate, listen, talk'),[L('Zone de saisie','Message box'),'🎙'],L('Dictée avec le micro, mode vocal avec le bouton rond, lecture des réponses avec l’icône haut-parleur.','Dictation with the mic, voice mode with the round button, read-aloud with the speaker icon.'),[L('Micro : parlez, relisez le texte, envoyez.','Mic: speak, review the text, send.'),L('Bouton rond (zone vide) : conversation orale.','Round button (empty box): spoken conversation.'),L('Sous une réponse : haut-parleur pour l’écouter.','Under an answer: speaker to listen.')],null);
 if(ft('multiple_models'))F(G2,'multi','layers',L('Comparer des modèles','Compare models'),L('Plusieurs réponses côte à côte','Several answers side by side'),[L('Sélecteur d’assistant','Assistant selector'),'+'],L('Posez la même question à plusieurs modèles et comparez.','Ask several models the same question and compare.'),[L('Ajoutez un second modèle à côté du sélecteur.','Add a second model next to the selector.'),L('Envoyez une seule fois : chaque modèle répond.','Send once: each model answers.')],null,L('Plusieurs réponses identiques peuvent partager la même erreur.','Matching answers can share the same mistake.'));
 if(av('prompts'))F(G2,'prompts','slash',L('Modèles de demande','Saved prompts'),L('Tapez /','Type /'),[L('Zone de saisie','Message box'),'/'],L('Insère une demande préparée à l’avance.','Inserts a request prepared in advance.'),[L('Tapez / au début du message.','Type / at the start of the message.'),L('Choisissez la commande et complétez les champs.','Pick the command and fill in the fields.')],null);
 if(ft('notes'))F(G3,'notes','book','Notes',L('Rédiger avec l’assistant','Write with the assistant'),[L('Barre latérale','Sidebar'),'Notes'],L('Des documents persistants avec un assistant qui peut les modifier directement.','Lasting documents with an assistant that can edit them directly.'),[L('Notes › Create.','Notes › Create.'),L('Ouvrez le panneau Chat et utilisez une suggestion.','Open the Chat panel and use a suggestion.'),L('⋯ › Pin to Sidebar pour la garder à portée.','⋯ › Pin to Sidebar to keep it handy.')],L('Extrais les actions de cette note avec responsable et échéance.','Extract action items from this note with owner and deadline.'));
 if(ft('folders'))F(G3,'folders','folder',L('Dossiers','Folders'),L('Projets avec consignes et documents','Projects with instructions and documents'),[L('Barre latérale','Sidebar'),'Folders','+'],L('Rangez les conversations d’un projet et partagez consignes et documents entre elles.','Group a project’s chats and share instructions and documents between them.'),[L('Folders › +, donnez un nom.','Folders › +, give a name.'),L('Ajoutez un System Prompt et des documents.','Add a System Prompt and documents.'),L('Démarrez vos conversations depuis le dossier.','Start chats from the folder.')],null);
 if(ft('channels'))F(G3,'channels','hash',L('Canaux','Channels'),L('Discussions d’équipe avec l’IA','Team discussions with AI'),[L('Barre latérale','Sidebar'),'Channels'],L('Espaces partagés où l’IA répond quand on la mentionne.','Shared spaces where AI answers when mentioned.'),[L('Ouvrez un canal.','Open a channel.'),L('Tapez @ et choisissez un assistant.','Type @ and pick an assistant.'),L('Répondez en fil pour séparer les sujets.','Reply in threads to separate topics.')],null,L('Tous les membres voient vos messages.','All members see your messages.'));
 F(G3,'feedback','tup',L('Avis sur les réponses','Rating answers'),L('👍 / 👎 sous chaque réponse','👍 / 👎 under each answer'),[L('Sous une réponse','Under an answer'),'👍 👎'],L('Notez les réponses pour aider à améliorer les assistants.','Rate answers to help improve assistants.'),[L('Cliquez 👍 ou 👎.','Click 👍 or 👎.'),L('Choisissez une note et une raison.','Pick a score and a reason.'),L('Ajoutez une phrase précise, puis Save.','Add one precise sentence, then Save.')],null);
 if(ft('memories'))F(G4,'memory','brain',L('Mémoire','Memory'),L('Préférences retenues','Remembered preferences'),[L('Votre nom','Your name'),'Settings','Personalization'],L('L’assistant retient des informations utiles d’une conversation à l’autre.','The assistant keeps useful details across chats.'),[L('Ouvrez Settings › Personalization › Memory.','Open Settings › Personalization › Memory.'),L('Ajoutez, corrigez ou supprimez ce qui est retenu.','Add, edit or delete what is remembered.')],null,L('N’y enregistrez aucune donnée sensible.','Do not store sensitive data there.'));
 F(G4,'settings','sliders',L('Paramètres','Settings'),L('Langue, thème, compte','Language, theme, account'),[L('Votre nom (en bas à gauche)','Your name (bottom left)'),'Settings'],L('Réglez l’interface selon vos préférences.','Adjust the interface to your preferences.'),[L('Cliquez sur votre nom en bas de la barre latérale.','Click your name at the bottom of the sidebar.'),L('Settings › General pour la langue et le thème.','Settings › General for language and theme.')],null,L('Déconnectez-vous sur un ordinateur partagé.','Sign out on a shared computer.'));
 if(ft('calendar'))F(G3,'calendar','calendar',L('Calendrier','Calendar'),L('Événements et rappels','Events and reminders'),[L('Menu utilisateur','User menu'),'Calendar'],L('Agenda personnel avec vues mois/semaine/jour, événements récurrents, rappels et partage.','Personal calendar with month/week/day views, recurring events, reminders and sharing.'),[L('Menu utilisateur › Calendar.','User menu › Calendar.'),L('New Event : titre, date, lieu, répétition, rappel.','New Event: title, date, location, repeat, reminder.'),L('Ou demandez à l’assistant : « ajoute le partiel de stats jeudi 14h ».','Or ask the assistant: “add the stats exam on Thursday at 2pm”.')],L('Qu’est-ce que j’ai dans mon calendrier cette semaine ?','What is on my calendar this week?'),L('Aucune synchronisation avec Outlook ou Google Agenda.','No sync with Outlook or Google Calendar.'));
 if(ft('automations'))F(G3,'automations','clock',L('Automatisations','Automations'),L('Demandes planifiées','Scheduled requests'),[L('Menu utilisateur','User menu'),'Automations'],L('Exécute une demande automatiquement (une fois, chaque heure, jour, semaine, mois). Chaque exécution crée une conversation.','Runs a request automatically (once, hourly, daily, weekly, monthly). Each run creates a chat.'),[L('Automations › Create.','Automations › Create.'),L('Title, Instructions, Model, Schedule, Folder.','Title, Instructions, Model, Schedule, Folder.'),L('Testez avec Run now, suivez les Execution logs.','Test with Run now, check Execution logs.')],L('Programme un résumé des actualités du secteur chaque lundi à 8h.','Schedule an industry news digest every Monday at 8am.'),L('Relisez les résultats : l’automatisation s’exécute sans vous.','Review results: the automation runs without you.'));
 F(G2,'builtin','spark',L('Outils intégrés','Built-in tools'),L('L’assistant agit pour vous','The assistant acts for you'),[L('Dans la conversation','In the chat')],L('Date et heure, recherche web, documents, mémoire, notes, anciennes conversations, calendrier, automatisations, images, code, listes de tâches : selon l’assistant et vos droits.','Date & time, web search, documents, memory, notes, past chats, calendar, automations, images, code, task lists: depending on the assistant and your rights.'),[L('Demandez en langage naturel.','Ask in plain language.'),L('Repérez la ligne « outil utilisé » et ouvrez-la pour vérifier.','Look for the “tool used” line and open it to check.'),L('Utilisez Ask for approval pour valider les actions.','Use Ask for approval to confirm actions.')],L('Retrouve dans mes notes ce que j’ai écrit sur le projet Alpha.','Find what I wrote about Project Alpha in my notes.'));
 F(G4,'status','smile','Update your status',L('Statut visible par les autres','Status shown to others'),[L('Menu utilisateur','User menu'),'Update your status'],L('Un emoji et un court message pour indiquer votre disponibilité.','An emoji and a short message to show your availability.'),[L('Cliquez sur votre nom.','Click your name.'),L('Update your status, choisissez un emoji et un texte.','Update your status, pick an emoji and text.')],null);
 /*__ADMIN_ONLY_START__*/
 if(ADMIN){F(G4,'playground','code','Playground',L('Administrateurs','Administrators'),[L('Menu utilisateur','User menu'),'Playground'],L('Tester un modèle avec ses paramètres, hors conversation.','Test a model with its parameters, outside a chat.'),[L('Ouvrez Playground.','Open Playground.'),L('Choisissez un modèle, un prompt système et envoyez.','Pick a model, a system prompt and send.')],null);
  F(G4,'admin','shield','Admin Panel',L('Administrateurs','Administrators'),[L('Menu utilisateur','User menu'),'Admin Panel'],L('Utilisateurs, groupes, permissions, réglages et évaluations.','Users, groups, permissions, settings and evaluations.'),[L('Admin Panel › Users › Groups pour les permissions.','Admin Panel › Users › Groups for permissions.'),L('Testez chaque changement sur un compte non administrateur.','Test every change with a non-admin account.')],null);}
 /*__ADMIN_ONLY_END__*/
 ACTIONS.slice(0,6).forEach(function(a,k){F(G4,'action-'+k,k%2?'spark':'doc',a.name,L('Action sous les réponses','Action under answers'),[L('Sous une réponse','Under an answer'),L('icône d’action','action icon')],a.description||L('Action ajoutée par votre organisation.','Action added by your organization.'),[L('Survolez une réponse.','Hover an answer.'),L('Cliquez sur l’icône de l’action.','Click the action icon.')],null);});
 return x;}

/* ============ Render ============ */
function header(){
 if(!on('show_features_tab')){tab='tour';var lt=document.querySelector('.g-tabs');if(lt)lt.style.display='none';}
 if(!on('show_language_switch')){var lg=document.querySelector('.g-lang');if(lg)lg.style.display='none';}
 document.getElementById('gLogo').textContent=PRODUCT.charAt(0).toUpperCase();
 document.getElementById('gName').replaceChildren(document.createTextNode(PRODUCT+' '),E('span','',L('· Guide et tutoriels','· Guide & tutorials')));
 document.querySelectorAll('[data-tab]').forEach(function(b){b.textContent=b.dataset.tab==='tour'?L('Visite guidée','Guided tour'):L('Fonctions','Features');b.setAttribute('aria-selected',String(b.dataset.tab===tab));});
 document.querySelectorAll('[data-lang]').forEach(function(b){b.setAttribute('aria-pressed',String(b.dataset.lang===lang));});
 document.getElementById('close').replaceChildren(E('span','',L('Fermer','Close')),I('x'));
 document.documentElement.lang=lang;
}
function render(){header();var body=document.getElementById('gBody');body.replaceChildren();var ub=updateBanner();if(ub)add(body,ub);if(tab==='lib')renderLib(body);else renderTour(body);save({index:cur,lang:lang,tab:tab,status:'active'});requestAnimationFrame(height);}

function renderTour(body){
 var s=steps[cur];if(active===null)active=s.start||(s.notes[0]&&s.notes[0].a)||'';
 var chips=E('nav','g-chapters');chips.setAttribute('aria-label',L('Chapitres','Chapters'));
 chapters.forEach(function(c){var b=E('button','chip'+(cur>=c.start&&cur<=c.end?' on':cur>c.end?' done':''),c.name);b.type='button';b.addEventListener('click',function(){go(c.start);});add(chips,b);});
 var bar=E('div','g-bar'),fill=E('span');fill.style.width=((cur+1)/steps.length*100)+'%';add(bar,fill);
 var st=E('div','step'),ch=chapters.filter(function(c){return cur>=c.start&&cur<=c.end;})[0];
 add(st,add(E('p','kicker'),E('span','',s.ch),ch&&ch.end>ch.start?E('b','',(cur-ch.start+1)+'/'+(ch.end-ch.start+1)):null),E('h1','',s.title),E('p','desc',s.desc));
 if(s.notes.length)add(st,add(E('div','hint'),I('bolt'),E('span','',L('Cliquez sur l’écran ou sur une explication','Click the screen or an explanation'))));
 var stage=E('div','stage '+(s.stage||''));if(s.cover)renderCover(st);else add(stage,s.mock());
 stage.addEventListener('click',function(e){var t=e.target.closest('[data-a]');if(!t)return;active=t.getAttribute('data-a');renderStage(stage,s);syncNotes(st);});
 if(!s.cover)add(st,stage);
 if(s.notes.length){var ns=E('div','notes');s.notes.forEach(function(n,k){var b=E('button','note'+(active===n.a?' on':''));b.type='button';b.dataset.n=n.a;add(b,E('span','note-n',k+1),add(E('span','note-b'),add(E('span','note-k'),I(n.i),E('span','',n.k)),E('span','note-t',n.t)));
   b.addEventListener('click',function(){active=n.a;renderStage(stage,s);syncNotes(st);});add(ns,b);});add(st,ns);}
 if(s.compare)add(st,add(E('div','compare'),add(E('div','bad'),add(E('b'),I('noc'),E('span','',L('Avis peu utile','Unhelpful feedback'))),E('span','',s.compare[0])),add(E('div','good'),add(E('b'),I('okc'),E('span','',L('Avis utile','Helpful feedback'))),E('span','',s.compare[1]))));
 if(s.example&&on('show_try_buttons')){var ex=E('div','example'),tb=E('button','try');tb.type='button';add(tb,E('span','',L('Essayer dans le chat','Try it in the chat')),I('arrowr'));tb.addEventListener('click',function(){sendPrompt(s.example);});add(ex,E('small','',s.exampleLabel||L('Exemple','Example')),E('p','',s.example),tb);add(st,ex);}
 if(s.tip)add(st,add(E('div','callout tip'),I('bulb'),E('span','',s.tip)));
 if(s.info)add(st,add(E('div','callout info'),I('info'),E('span','',s.info)));
 if(s.warn)add(st,add(E('div','callout warn'),I('warn'),E('span','',s.warn)));
 if(s.links){var lk=data.links||{},wrap=E('div','callout info');var any=false;add(wrap,I('info'));var sp=E('span');[['support_url',L('Support','Support')],['acceptable_use_url',L('Règles d’utilisation','Acceptable use')],['privacy_url',L('Confidentialité','Privacy')],['feedback_url',L('Donner votre avis','Give feedback')]].forEach(function(x){if(lk[x[0]]){var a=E('a','',x[1]);a.href=lk[x[0]];a.target='_blank';a.rel='noopener noreferrer';a.style.cssText='margin-right:14px;font-weight:650;color:inherit';add(sp,a);any=true;}});add(wrap,sp);if(any)add(st,wrap);}
 var ft_=E('div','g-foot'),back=E('button','btn ghost'),next=E('button','btn primary'),last=cur===steps.length-1;
 back.type=next.type='button';add(back,I('arrowl'),E('span','',L('Précédent','Back')));back.disabled=cur===0;
 add(next,E('span','',last?L('Commencer','Get started'):L('Suivant','Next')),last?null:I('arrowr'));
 back.addEventListener('click',function(){go(cur-1);});next.addEventListener('click',function(){last?finish():go(cur+1);});
 add(ft_,back,E('span','g-count',L('Étape ','Step ')+(cur+1)+L(' sur ',' of ')+steps.length),next);
 add(body,chips,bar,st,ft_);
 setTimeout(function(){var c=chips.querySelector('.on');if(c&&c.scrollIntoView)c.scrollIntoView({block:'nearest',inline:'center'});},0);
}
function renderCover(st){
 var c=E('div','cover');add(c,E('h2','',L('Ce que vous allez apprendre','What you will learn')),E('p','',L('Écrire une bonne demande, ajouter des documents, activer les outils, organiser vos projets, noter les réponses et utiliser l’IA de façon responsable.','Writing good requests, adding documents, turning on tools, organizing projects, rating answers and using AI responsibly.')));
 var facts=E('div','facts');[['clock',L('Environ 10 minutes','About 10 minutes')],['map',chapters.length-1+' '+L('chapitres','chapters')],['user',L('Adapté à votre compte','Tailored to your account')],['check',L('Reprenez quand vous voulez','Resume anytime')]].forEach(function(f){add(facts,add(E('span','fact'),I(f[0]),E('span','',f[1])));});add(c,facts);
 var a=E('div','cover-actions'),b1=E('button','cbtn main'),b2=E('button','cbtn alt');b1.type=b2.type='button';add(b1,E('span','',L('Commencer la visite','Start the tour')),I('arrowr'));add(b2,E('span','',L('Voir toutes les fonctions','See all features')));
 b1.addEventListener('click',function(){go(1);});b2.addEventListener('click',function(){tab='lib';render();});add(a,b1,on('show_features_tab')?b2:null);add(c,a);add(st,c);
 add(st,E('p','toc-h',L('Sommaire','Contents')));var toc=E('div','toc');
 chapters.slice(1).forEach(function(ch){var d=CHD[ch.name]||{},b=E('button');b.type='button';var n=ch.end-ch.start+1;add(b,I(d.i||'info'),add(E('span'),E('b','',ch.name),E('span','',d.d||''),E('small','',n+' '+(n>1?L('écrans','screens'):L('écran','screen')))));b.addEventListener('click',function(){go(ch.start);});add(toc,b);});
 add(st,toc);
}
function updateBanner(){
 if(!updated)return null;var n=data.update_notes||{},txt=(lang==='fr'?n.fr:n.en)||L('Le guide a été enrichi. Parcourez le sommaire pour voir les nouveautés.','The guide has been updated. Browse the contents to see what is new.');
 var b=E('div','callout info');b.style.margin='14px 18px 0';var x=E('button','sheet-x');x.type='button';x.setAttribute('aria-label',L('Fermer','Close'));add(x,I('x'));
 add(b,I('spark'),add(E('span'),E('b','',L('Guide mis à jour. ','Guide updated. ')),txt),x);x.addEventListener('click',function(){updated=false;b.remove();requestAnimationFrame(height);});return b;}
function renderStage(stage,s){stage.replaceChildren(s.mock());requestAnimationFrame(height);}
function syncNotes(st){st.querySelectorAll('.note').forEach(function(b){b.classList.toggle('on',b.dataset.n===active);});}

function renderLib(body){
 var wrap=E('div','lib');add(wrap,add(E('p','kicker'),E('span','',L('Tutoriels','Tutorials'))),E('h1','',L('Toutes les fonctions disponibles pour vous','Every feature available to you')),E('p','desc',L('Cette liste s’adapte à votre compte. Ouvrez une fiche pour savoir où la trouver et comment bien l’utiliser.','This list adapts to your account. Open a card to see where to find it and how to use it well.')));
 var items=libItems(),groups=[];items.forEach(function(x){if(groups.indexOf(x.g)<0)groups.push(x.g);});
 groups.forEach(function(g){add(wrap,E('p','lib-group',g));var grid=E('div','lib-grid');items.filter(function(x){return x.g===g;}).forEach(function(x){var t=E('button','tile');t.type='button';add(t,add(E('span','tile-k'),I(x.i),E('span','',x.name)),E('span','tile-t',x.short),E('span','tile-w',x.where.join(' › ')));t.addEventListener('click',function(){sheet=x.id;renderSheet(wrap,x);});add(grid,t);});add(wrap,grid);});
 add(body,wrap);var open=items.filter(function(x){return x.id===sheet;})[0];if(open)renderSheet(wrap,open);
}
function renderSheet(wrap,x){var old=wrap.querySelector('.overlay');if(old)old.remove();
 var ov=E('div','overlay'),sh=E('div','sheet'),h=E('div','sheet-h'),close=E('button','sheet-x');close.type='button';close.setAttribute('aria-label',L('Fermer','Close'));add(close,I('x'));
 add(h,I(x.i),add(E('div'),E('h2','',x.name),E('p','',x.what)),close);
 var b=E('div','sheet-b'),path=E('div','path');x.where.forEach(function(w,k){if(k)add(path,I('right'));add(path,E('span','',w));});
 add(b,E('p','sheet-l',L('Où la trouver','Where to find it')),path,E('p','sheet-l',L('Comment l’utiliser','How to use it')));
 var ol=E('ol','steps');x.how.forEach(function(t){add(ol,E('li','',t));});add(b,ol);
 if(x.example){var ex=E('div','example'),tb=E('button','try');tb.type='button';add(tb,E('span','',L('Essayer dans le chat','Try it in the chat')),I('arrowr'));tb.addEventListener('click',function(){sendPrompt(x.example);});add(ex,E('small','',L('Exemple','Example')),E('p','',x.example),tb);add(b,ex);}
 if(x.note)add(b,add(E('div','callout warn'),I('warn'),E('span','',x.note)));
 add(sh,h,b);add(ov,sh);
 function shut(){sheet=null;ov.remove();requestAnimationFrame(height);}
 close.addEventListener('click',shut);ov.addEventListener('click',function(e){if(e.target===ov)shut();});
 add(wrap,ov);wrap.style.minHeight=Math.max(420,sh.offsetHeight+60)+'px';setTimeout(function(){wrap.style.minHeight=Math.max(420,sh.offsetHeight+60)+'px';requestAnimationFrame(height);close.focus();},0);
}

function go(i){cur=Math.max(0,Math.min(steps.length-1,i));active=null;render();}
function finish(){save({status:'completed',index:cur});var g=document.getElementById('g');g.classList.add('slim');
 var r=document.querySelector('.g-right');r.replaceChildren();var again=E('button','g-close',L('Rouvrir le guide','Reopen guide'));again.type='button';again.addEventListener('click',function(){g.classList.remove('slim');save({status:'active'});location.reload();});
 add(r,E('span','slim-msg',L('Guide fermé','Guide closed')),again);requestAnimationFrame(height);}

function initLanguageButtons(){var wrap=document.querySelector('.g-lang');if(!wrap)return;SUPPORTED.forEach(function(code){if(wrap.querySelector('[data-lang="'+code+'"]'))return;var b=E('button','',code.toUpperCase());b.type='button';b.dataset.lang=code;b.title=LOCALE_NAMES[code]||code;add(wrap,b);});wrap.querySelectorAll('[data-lang]').forEach(function(b){b.title=LOCALE_NAMES[b.dataset.lang]||b.dataset.lang;b.addEventListener('click',function(){if(lang===b.dataset.lang)return;lang=b.dataset.lang;build();active=null;render();});});}
initLanguageButtons();
document.querySelectorAll('[data-tab]').forEach(function(b){b.addEventListener('click',function(){tab=b.dataset.tab;sheet=null;render();});});
document.getElementById('close').addEventListener('click',finish);
document.addEventListener('keydown',function(e){if(e.key==='Escape'&&sheet){sheet=null;render();return;}if(tab!=='tour'||document.getElementById('g').classList.contains('slim'))return;if(e.key==='ArrowRight')go(cur+1);if(e.key==='ArrowLeft')go(cur-1);});

(function start(){var st=load();
 if(st.rev&&st.rev!==REV){updated=true;st.status='active';st.index=0;save({status:'active',index:0});}
 save({rev:REV});if(SUPPORTED.indexOf(st.lang)>=0)lang=st.lang;build();
 cur=Number.isInteger(st.index)?Math.max(0,Math.min(steps.length-1,st.index)):0;if(st.tab==='lib')tab='lib';
 render();if(st.status==='completed')finish();
 window.addEventListener('load',height);if('ResizeObserver' in window)new ResizeObserver(height).observe(document.body);})();
}());
</script>
</body>
</html>"""

FEATURE_DEFINITIONS = {
    "web_search": ("Recherche web", "Web search"),
    "image_generation": ("Génération d’images", "Image generation"),
    "code_interpreter": ("Interpréteur de code", "Code interpreter"),
    "file_upload": ("Téléversement de fichiers", "File upload"),
    "notes": ("Notes", "Notes"),
    "folders": ("Dossiers", "Folders"),
    "channels": ("Canaux", "Channels"),
    "memories": ("Mémoire", "Memory"),
    "automations": ("Automatisations", "Automations"),
    "calendar": ("Calendrier", "Calendar"),
    "multiple_models": ("Comparaison de modèles", "Multiple models"),
    "stt": ("Transcription vocale", "Speech to text"),
    "tts": ("Lecture vocale", "Text to speech"),
    "call": ("Conversation vocale", "Voice call"),
}

# Permissions that live under "chat" instead of "features" in Open WebUI.
CHAT_PERMISSION_KEYS = {"file_upload", "multiple_models", "stt", "tts", "call"}

# Instance-wide switches. If the administrator turned a feature off for the whole
# platform, the guide hides it for everyone, admins included.
GLOBAL_FEATURE_FLAGS = {
    "web_search": ("ENABLE_WEB_SEARCH", "ENABLE_RAG_WEB_SEARCH"),
    "image_generation": ("ENABLE_IMAGE_GENERATION",),
    "code_interpreter": ("ENABLE_CODE_INTERPRETER",),
    "notes": ("ENABLE_NOTES",),
    "channels": ("ENABLE_CHANNELS",),
    "calendar": ("ENABLE_CALENDAR",),
    "automations": ("ENABLE_AUTOMATIONS",),
    "folders": ("ENABLE_FOLDERS",),
    "memories": ("ENABLE_MEMORIES",),
}

# Every section switch exposed as a valve and forwarded to the page.
SECTION_FLAGS = [
    "show_cover", "show_welcome", "show_composer", "show_plus_menu", "show_tool_permissions",
    "show_integrations", "show_tools", "show_web_search", "show_models", "show_suggestions",
    "show_response_actions", "show_feedback", "show_sidebar_navigation", "show_user_menu",
    "show_notes", "show_folders", "show_channels", "show_calendar", "show_automations",
    "show_builtin_tools", "show_admin_section", "show_safety", "show_file_upload", "show_knowledge",
    "show_prompts", "show_memory", "show_image_generation", "show_code_interpreter", "show_voice",
    "show_multiple_models", "show_features_tab", "show_language_switch", "show_try_buttons",
]


class Event:
    class Valves(BaseModel):
        # ----------------------------------------------------------------- Master
        enabled: bool = Field(
            True,
            description="MASTER SWITCH. When OFF, the function does nothing at all: no guide is created, updated or deployed. Existing welcome chats stay as they are.",
        )
        production_enabled: bool = Field(
            False,
            description="SAFETY SWITCH FOR REAL USERS. Keep OFF while you validate the guide with the test account (see Testing valves). When OFF, sign-up, login and deployment events are ignored for everyone except the test user.",
        )

        # --------------------------------------------------------------- Delivery
        create_on_signup: bool = Field(
            True,
            description="NEW USERS. Create the guide as soon as an account is created (self sign-up, admin-created account, SSO/LDAP first login). Pending accounts are handled by 'skip_pending_users'.",
        )
        create_on_first_login: bool = Field(
            True,
            description="EXISTING USERS. If a user has never received the guide, create it at their next login. Covers accounts created before this function was installed and pending users who were approved later.",
        )
        create_on_approval: bool = Field(
            True,
            description="APPROVED USERS. Create the guide immediately when an account role changes from pending to user or admin. Login remains a fallback if the role-change event is unavailable.",
        )
        skip_pending_users: bool = Field(
            True,
            description="Do not create a guide for accounts still in 'pending' role (they cannot use the platform yet). They receive it at their first login after approval.",
        )
        include_admins: bool = Field(
            True,
            description="Also deliver the guide to administrators. Admins see an extra 'Administration' chapter.",
        )
        deploy_to_all_users: bool = Field(
            False,
            description="SEND TO EVERYONE. When ON and you increase 'deployment_revision', every eligible user who does not have the guide yet receives it immediately in their sidebar. When OFF, a deployment only updates guides that already exist.",
        )
        deployment_revision: int = Field(
            0,
            ge=0,
            description="PUSH NOW. Increase this number and save to run a background deployment: existing guides are updated in place (no duplicate chat) and, if 'deploy_to_all_users' is ON, missing guides are created. Users already processed for this number are skipped, so saving twice or restarting the server is safe.",
        )
        deployment_batch_size: int = Field(
            50, ge=1, le=500, description="How many users are processed before pausing during a deployment. Lower it on small servers."
        )
        deployment_batch_delay_seconds: float = Field(
            0.5, ge=0, le=30, description="Pause between two batches during a deployment, to keep the server responsive."
        )
        recreate_deleted_guides: bool = Field(
            False,
            description="If a user deleted their welcome chat: OFF = respect their choice and never recreate it. ON = recreate it at the next login or deployment.",
        )

        # ---------------------------------------------------------------- Updates
        guide_revision: int = Field(
            1,
            ge=1,
            description="CONTENT VERSION. Increase it when you change the guide (texts, links, sections). Each user's existing guide is updated in place at their next login (or immediately with a deployment), and the guide shows a 'Guide updated' banner once, even if they had already finished it.",
        )
        update_notes_en: str = Field(
            "",
            max_length=500,
            description="Optional English text shown in the 'Guide updated' banner after a revision change, e.g. 'New: Calendar and Automations chapters.' Leave empty for a generic message.",
        )
        update_notes_fr: str = Field(
            "",
            max_length=500,
            description="Optional French text for the 'Guide updated' banner, e.g. 'Nouveau : chapitres Calendrier et Automatisations.'",
        )
        refresh_on_login: bool = Field(
            True,
            description="At login, update the user's guide when their permissions, tools or your valves have changed. A new 'guide_revision' is always applied at login, whatever the interval.",
        )
        refresh_interval_minutes: int = Field(
            60,
            ge=0,
            le=10080,
            description="Minimum time between two permission checks for the same user at login. 0 = check at every login.",
        )

        # ------------------------------------------------------------ Welcome chat
        title_emoji: str = Field("👋", max_length=8, description="Emoji placed in the chat title. Leave empty for no emoji.")
        welcome_title_en: str = Field(
            "{emoji} Welcome to {product}",
            max_length=120,
            description="English chat title shown in the sidebar. Placeholders: {emoji}, {product}.",
        )
        welcome_title_fr: str = Field(
            "{emoji} Bienvenue sur {product}",
            max_length=120,
            description="French chat title shown in the sidebar. Placeholders: {emoji}, {product}.",
        )
        update_title_on_refresh: bool = Field(
            True, description="When a guide is updated, also apply the current title (useful after changing the emoji or product name)."
        )
        pin_welcome_chat: bool = Field(
            True, description="Pin the welcome chat at the top of the user's sidebar when it is created. Users can unpin it; it is never re-pinned afterwards."
        )
        default_language: str = Field(
            "fr",
            min_length=2,
            max_length=12,
            pattern=r"^[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})?$",
            description="Fallback locale when the user's Open WebUI language is unavailable. It must exist in the embedded locale catalog.",
        )
        use_user_interface_language: bool = Field(
            True, description="Use each user's Open WebUI interface language (Settings > General) when that locale is embedded in the guide."
        )
        preferred_welcome_model_id: str = Field(
            "", description="Model ID attached to the welcome chat. Only used if the user is allowed to access it; otherwise their first accessible model is used."
        )
        default_group_id: str = Field(
            "", description="Optional group ID added to brand-new accounts before their permissions are read. Leave empty to disable. Never applied to existing users."
        )

        # ---------------------------------------------------------------- Branding
        product_name: str = Field("AI Assistant", max_length=60, description="Platform name shown in the guide and title.")
        organization_name: str = Field(
            "", max_length=120, description="Organization name shown under the product name. Leave empty to show none."
        )
        primary_color: str = Field("#1F4E79", description="Main brand colour (#RRGGBB): header, primary buttons. Set it to your own brand colour.")
        secondary_color: str = Field("#0EA5B7", description="Accent colour (#RRGGBB): highlights, progress, dark-mode buttons.")
        support_url: str = Field("", description="HTTPS link to your support page, shown on the last step. Non-HTTPS links are ignored.")
        privacy_url: str = Field("", description="HTTPS link to your privacy policy.")
        acceptable_use_url: str = Field("", description="HTTPS link to your AI acceptable-use rules.")
        feedback_url: str = Field("", description="HTTPS link to a feedback form about the guide.")

        # ------------------------------------------------------ Sections (on / off)
        show_cover: bool = Field(True, description="Cover page: what the guide is, key facts and clickable table of contents.")
        show_welcome: bool = Field(True, description="'Start' chapter: home screen, message box and suggested questions.")
        show_composer: bool = Field(True, description="Message box controls: +, Integrations, model selector, dictation, voice/send.")
        show_plus_menu: bool = Field(True, description="The + menu with every item and its sub-lists (only items the user can use).")
        show_tool_permissions: bool = Field(True, description="Full access vs Ask for approval. Only shown to users who have tools.")
        show_integrations: bool = Field(True, description="Integrations button: tools, toggle functions, web search, code interpreter.")
        show_tools: bool = Field(True, description="Tools sub-menu with the user's own tools.")
        show_web_search: bool = Field(True, description="Web search step and card. Hidden anyway if web search is disabled for the user.")
        show_models: bool = Field(True, description="Model selector: search, info icon, Set as default.")
        show_suggestions: bool = Field(True, description="Suggested questions step.")
        show_response_actions: bool = Field(True, description="Icons under each answer, including the user's action functions.")
        show_feedback: bool = Field(True, description="Positive and negative feedback steps.")
        show_sidebar_navigation: bool = Field(True, description="Sidebar chapter (only items the user has).")
        show_user_menu: bool = Field(True, description="User menu chapter: status, Workspace, Notes, Calendar, Automations, Settings, Sign Out.")
        show_notes: bool = Field(True, description="Notes chapter (list, editor, ⋯ menu). Requires Notes permission.")
        show_folders: bool = Field(True, description="Folders chapter. Requires Folders to be enabled.")
        show_channels: bool = Field(True, description="Channels chapter. Requires Channels permission.")
        show_calendar: bool = Field(True, description="Calendar chapter. Requires Calendar permission.")
        show_automations: bool = Field(True, description="Automations chapter. Requires Automations permission.")
        show_builtin_tools: bool = Field(True, description="Built-in tools chapter (date, notes, calendar, memory… only the ones the user has).")
        show_admin_section: bool = Field(True, description="Administration chapter (Admin Panel, Playground). Only ever shown to admins.")
        show_safety: bool = Field(True, description="Responsible use chapter.")
        show_file_upload: bool = Field(True, description="File upload card in the Features tab.")
        show_knowledge: bool = Field(True, description="Knowledge card and # shortcut. Requires access to at least one knowledge base.")
        show_prompts: bool = Field(True, description="Saved prompts (/) card. Requires access to at least one prompt.")
        show_memory: bool = Field(True, description="Memory card. Requires Memory permission.")
        show_image_generation: bool = Field(True, description="Image generation card. Requires permission.")
        show_code_interpreter: bool = Field(True, description="Code interpreter card. Requires permission.")
        show_voice: bool = Field(True, description="Voice card (dictation, voice mode, read aloud).")
        show_multiple_models: bool = Field(True, description="Compare models card. Requires permission.")
        show_features_tab: bool = Field(True, description="'Features' tab with every available feature as a card and pop-up tutorial.")
        show_language_switch: bool = Field(True, description="FR / EN switch in the guide header.")
        show_try_buttons: bool = Field(True, description="'Try it in the chat' buttons that place an example in the message box (never sends it).")

        # --------------------------------------------------------------- Privacy
        expose_tool_names: bool = Field(
            True, description="Show the names of the user's own tools in the guide. Only tools this user can already access are listed; never schemas, valves or secrets."
        )
        expose_function_names: bool = Field(
            True, description="Show the names of the user's toggle functions and response actions. Only from models this user can access."
        )
        max_items_per_section: int = Field(8, ge=1, le=30, description="Maximum tools, functions or actions listed per menu.")
        catalog_timeout_seconds: int = Field(
            20, ge=3, le=60, description="Maximum time to read one permission source. On timeout that source is treated as empty (hidden), never as allowed."
        )

        # ---------------------------------------------------------------- Testing
        test_user: str = Field("", description="Email or user ID of a test account. Works even when 'production_enabled' is OFF.")
        test_revision: int = Field(
            0, ge=0, description="Increase and save to (re)build the guide for the test user. Each increase replaces their previous test guide instead of piling up chats."
        )
        test_assign_group: bool = Field(False, description="Also add the test user to 'default_group_id' during the test.")

    def __init__(self):
        self.valves = self.Valves()
        self._redis = self._connect_redis()
        self._prefix = self._redis_prefix()
        self._local_locks: set[str] = set()
        self._tasks: set = set()

    # ================================================================ Events
    async def event(
        self,
        event: dict,
        __event_id__: str = None,
        __event_name__: str = None,
        __id__: str = None,
        __app__=None,
        __request__=None,
        **kwargs,
    ):
        try:
            if not self.valves.enabled:
                return

            if __event_name__ in ("auth.signup", "user.created"):
                user_id = self._user_id_from_event(__event_name__, event)
                if user_id and self.valves.create_on_signup and await self._allowed_target(user_id):
                    await self._ensure_guide(user_id, __app__, __request__, source=__event_name__, allow_create=True, assign_group=True)
                return

            if __event_name__ == "auth.login":
                user_id = self._user_id_from_event(__event_name__, event)
                if user_id and await self._allowed_target(user_id):
                    await self._handle_login(user_id, __app__, __request__)
                return

            if __event_name__ == "user.role_updated":
                user_id = self._user_id_from_event(__event_name__, event)
                data = event.get("data") if isinstance(event, dict) else None
                new_role = str(data.get("role") or "").strip().lower() if isinstance(data, dict) else ""
                if new_role and new_role not in {"user", "admin"}:
                    return
                if user_id and self.valves.create_on_approval and await self._allowed_target(user_id):
                    await self._ensure_guide(
                        user_id,
                        __app__,
                        __request__,
                        source="user.role_updated",
                        allow_create=True,
                        assign_group=True,
                    )
                return

            if __event_name__ == "function.valves_updated":
                subject_id = str((event.get("subject") or {}).get("id") or "")
                if __id__ and subject_id == str(__id__):
                    await self._maybe_run_test(__app__, __request__)
                    if self.valves.production_enabled and int(self.valves.deployment_revision) > 0:
                        self._spawn(self._deploy(__app__, None if __app__ is not None else __request__, int(self.valves.deployment_revision)))
        except Exception:
            log.exception("[onboarding] unhandled event=%s id=%s", __event_name__, __event_id__)

    async def _allowed_target(self, user_id: str) -> bool:
        """Production users need production_enabled; the test user is always allowed."""
        if self.valves.production_enabled:
            return True
        target = (self.valves.test_user or "").strip()
        return bool(target) and (await self._resolve_user(target)) == user_id

    async def _handle_login(self, user_id: str, app, request) -> None:
        marker = await self._get_marker(user_id)
        if not self._marker_valid(marker):
            if self.valves.create_on_first_login:
                await self._ensure_guide(user_id, app, request, source="auth.login", allow_create=True)
            return
        if not self.valves.refresh_on_login:
            return
        content_changed = (
            int(marker.get("guide_revision", 0)) != int(self.valves.guide_revision)
            or int(marker.get("template_revision", 0)) != TEMPLATE_REVISION
        )
        age = int(time.time()) - int(marker.get("refreshed_at", marker.get("created_at", 0)) or 0)
        if content_changed or age >= int(self.valves.refresh_interval_minutes) * 60:
            await self._ensure_guide(user_id, app, request, source="auth.login", allow_create=self.valves.recreate_deleted_guides)

    # ============================================================ Deployment
    def _spawn(self, coroutine) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _deploy(self, app, request, revision: int) -> None:
        job_key = f"{self._prefix}:secure-onboarding:deploy:{revision}"
        token = self._acquire_key(job_key, ttl=6 * 3600)
        if token is None:
            log.info("[onboarding] deployment %s already running", revision)
            return
        created = updated = skipped = failed = 0
        try:
            from open_webui.models.users import Users

            batch = int(self.valves.deployment_batch_size)
            skip = 0
            while True:
                page = await self._maybe_await(Users.get_users(skip=skip, limit=batch))
                users = page.get("users", []) if isinstance(page, dict) else list(page or [])
                if not users:
                    break
                for user in users:
                    user_id = str(self._value(user, "id", ""))
                    if not user_id:
                        continue
                    try:
                        marker = await self._get_marker(user_id)
                        if marker and int(marker.get("deployment_revision", 0)) >= revision:
                            skipped += 1
                            continue
                        allow_create = bool(self.valves.deploy_to_all_users) and (
                            not (marker or {}).get("deleted_by_user") or self.valves.recreate_deleted_guides
                        )
                        result = await self._ensure_guide(
                            user_id, app, request, source=f"deploy:{revision}", allow_create=allow_create, deployment_revision=revision
                        )
                        if result == "created":
                            created += 1
                        elif result == "updated":
                            updated += 1
                        else:
                            skipped += 1
                    except Exception:
                        failed += 1
                        log.exception("[onboarding] deployment %s failed for user=%s", revision, user_id)
                if len(users) < batch:
                    break
                skip += batch
                await asyncio.sleep(float(self.valves.deployment_batch_delay_seconds))
            log.info(
                "[onboarding] deployment %s done: created=%s updated=%s skipped=%s failed=%s",
                revision, created, updated, skipped, failed,
            )
        finally:
            self._release_key(job_key, token)

    # ======================================================== Core delivery
    async def _ensure_guide(
        self,
        user_id: str,
        app,
        request,
        source: str,
        allow_create: bool,
        assign_group: bool = False,
        deployment_revision: Optional[int] = None,
        force: bool = False,
    ) -> str:
        """Guarantee at most one guide per user, create or update it. Returns created|updated|unchanged|skipped."""
        from open_webui.models.chats import Chats
        from open_webui.models.users import Users

        token = self._acquire_key(self._lock_key(user_id), ttl=300)
        if token is None:
            return "skipped"
        try:
            user = await Users.get_user_by_id(user_id)
            if user is None:
                return "skipped"
            role = str(getattr(user, "role", "user") or "user")
            if role == "pending" and self.valves.skip_pending_users:
                return "skipped"
            if role == "admin" and not self.valves.include_admins:
                return "skipped"

            marker = await self._get_marker(user_id)
            chat = None
            if self._marker_valid(marker):
                chat = await Chats.get_chat_by_id_and_user_id(str(marker["chat_id"]), user_id)
                if chat is None:
                    if not (self.valves.recreate_deleted_guides and allow_create):
                        new_marker = {**marker, "deleted_by_user": True}
                        if deployment_revision is not None:
                            new_marker["deployment_revision"] = deployment_revision
                        await self._save_marker(user_id, new_marker)
                        return "skipped"
                    marker = None

            if chat is None:
                if not allow_create:
                    return "skipped"
                if assign_group and (self.valves.default_group_id or "").strip():
                    await self._assign_group(user_id)
                    user = await Users.get_user_by_id(user_id) or user
                snapshot = await self._build_snapshot(user, app, request)
                created = await self._create_welcome_chat(user, snapshot)
                if not created:
                    return "skipped"
                created.update({"version": ONBOARDING_VERSION, "source": source})
                if deployment_revision is not None:
                    created["deployment_revision"] = deployment_revision
                await self._save_marker(user_id, created)
                log.info("[onboarding] created user=%s source=%s", user_id, source)
                return "created"

            snapshot = await self._build_snapshot(user, app, request)
            new_hash = self._snapshot_hash(snapshot)
            now = int(time.time())
            new_marker = {
                **marker,
                "refreshed_at": now,
                "catalog_hash": new_hash,
                "guide_revision": int(self.valves.guide_revision),
                "template_revision": TEMPLATE_REVISION,
                "deleted_by_user": False,
            }
            if deployment_revision is not None:
                new_marker["deployment_revision"] = deployment_revision
            changed = force or new_hash != marker.get("catalog_hash")
            if changed:
                message_id = str(marker["message_id"])
                stored = (getattr(chat, "chat", None) or {}).get("history", {}).get("messages", {}).get(message_id, {})
                message = {**stored, "content": self._fallback_text(snapshot), "embeds": [self._render_html(snapshot)]}
                result = await Chats.upsert_message_to_chat_by_id_and_message_id(str(marker["chat_id"]), message_id, message, touch=False)
                if result is None:
                    log.warning("[onboarding] update failed user=%s", user_id)
                    return "skipped"
                if self.valves.update_title_on_refresh:
                    await self._set_title(str(marker["chat_id"]), self._title(snapshot))
            await self._save_marker(user_id, new_marker)
            return "updated" if changed else "unchanged"
        finally:
            self._release_key(self._lock_key(user_id), token)

    async def _create_welcome_chat(self, user, snapshot: dict) -> Optional[dict]:
        from open_webui.models.chats import ChatForm, Chats

        now = int(time.time())
        chat_id = str(uuid.uuid4())
        message_id = str(uuid.uuid4())
        model_id = snapshot.get("selected_model_id") or ""
        message = {
            "id": message_id,
            "parentId": None,
            "childrenIds": [],
            "role": "assistant",
            "content": self._fallback_text(snapshot),
            "embeds": [self._render_html(snapshot)],
            "files": [],
            "sources": [],
            "model": model_id,
            "modelName": model_id,
            "modelIdx": 0,
            "timestamp": now,
            "done": True,
        }
        chat = {
            "id": "",
            "title": self._title(snapshot),
            "models": [model_id] if model_id else [],
            "params": {},
            "history": {"messages": {message_id: message}, "currentId": message_id},
            "messages": [message],
            "tags": [],
            "timestamp": now * 1000,
            "meta": {"secure_onboarding": {"version": ONBOARDING_VERSION, "created_at": now}},
        }
        result = await Chats.insert_new_chat(chat_id, str(user.id), ChatForm(chat=chat))
        if result is None:
            return None
        if self.valves.pin_welcome_chat:
            await self._pin(chat_id, str(user.id))
        return {
            "chat_id": chat_id,
            "message_id": message_id,
            "created_at": now,
            "refreshed_at": now,
            "catalog_hash": self._snapshot_hash(snapshot),
            "guide_revision": int(self.valves.guide_revision),
            "template_revision": TEMPLATE_REVISION,
        }

    async def _set_title(self, chat_id: str, title: str) -> None:
        try:
            from open_webui.models.chats import Chats

            method = getattr(Chats, "update_chat_title_by_id", None)
            if method:
                await self._maybe_await(method(chat_id, title))
        except Exception:
            log.warning("[onboarding] title update not supported", exc_info=True)

    async def _pin(self, chat_id: str, user_id: str) -> None:
        try:
            from open_webui.models.chats import Chats

            chat = await Chats.get_chat_by_id_and_user_id(chat_id, user_id)
            method = getattr(Chats, "toggle_chat_pinned_by_id", None)
            if chat is not None and method and not getattr(chat, "pinned", False):
                await self._maybe_await(method(chat_id))
        except Exception:
            log.warning("[onboarding] pinning not supported", exc_info=True)

    # ============================================================== Snapshot
    async def _build_snapshot(self, user, app, request) -> dict:
        req = self._usable_request(app, request)
        timeout = int(self.valves.catalog_timeout_seconds)
        role = str(getattr(user, "role", "user") or "user")
        is_admin = role == "admin"

        async def safe(name: str, coroutine, fallback):
            # Any failure hides the resource. We never fall back to "allowed".
            try:
                return await asyncio.wait_for(coroutine, timeout=timeout)
            except Exception as exc:
                log.warning("[onboarding] catalog source=%s user=%s hidden: %s", name, user.id, type(exc).__name__)
                return fallback

        permissions, models, prompts, tools, knowledge = await asyncio.gather(
            safe("permissions", self._load_permissions(user), {}),
            safe("models", self._load_models(req, user), []),
            safe("prompts", self._load_prompts(user), []),
            safe("tools", self._load_tools(req, user), []),
            safe("knowledge", self._load_knowledge(user), []),
        )

        features = self._effective_features(permissions, is_admin, req)
        enabled = {f["key"] for f in features}
        workspace_perms = (permissions.get("workspace") or {}) if isinstance(permissions, dict) else {}
        workspace = {k: bool(is_admin or workspace_perms.get(k, False)) for k in ("models", "prompts", "skills", "tools", "knowledge")}
        max_items = int(self.valves.max_items_per_section)

        resources = {
            "features": features,
            "tools": [self._named(x) for x in tools[:max_items]] if self.valves.expose_tool_names else [],
            "toggles": self._unique_functions(models, "filters", max_items),
            "actions": self._unique_functions(models, "actions", max_items),
        }

        preferred = (self.valves.preferred_welcome_model_id or "").strip()
        model_ids = [str(self._value(m, "id", "")) for m in models if self._value(m, "id", "")]
        selected_model_id = preferred if preferred in model_ids else (model_ids[0] if model_ids else "")

        ui = {flag: bool(getattr(self.valves, flag)) for flag in SECTION_FLAGS}
        ui["default_language"] = self._language_for(user)
        ui["is_admin"] = is_admin
        # A section switch can never reveal something the user has no right to.
        ui["show_admin_section"] = ui["show_admin_section"] and is_admin

        now = int(time.time())
        return {
            "schema": 2,
            "template_revision": TEMPLATE_REVISION,
            "guide_revision": int(self.valves.guide_revision),
            "update_notes": {
                "fr": self._plain(self.valves.update_notes_fr, 500),
                "en": self._plain(self.valves.update_notes_en, 500),
            },
            "generated_at": now,
            "brand": {
                "product": self._plain(self.valves.product_name, 60) or "AI Assistant",
                "organization": self._plain(self.valves.organization_name, 120),
                "primary": self._safe_color(self.valves.primary_color, "#1F4E79"),
                "secondary": self._safe_color(self.valves.secondary_color, "#0EA5B7"),
            },
            "progress_scope": hashlib.sha256(f"{user.id}:{ONBOARDING_VERSION}".encode("utf-8")).hexdigest()[:20],
            "ui": ui,
            "links": {
                "support_url": self._safe_http_url(self.valves.support_url),
                "privacy_url": self._safe_http_url(self.valves.privacy_url),
                "acceptable_use_url": self._safe_http_url(self.valves.acceptable_use_url),
                "feedback_url": self._safe_http_url(self.valves.feedback_url),
            },
            "localization": {
                "supported": list(SUPPORTED_LOCALES),
                "names": dict(LOCALE_NAMES),
                "translations": self._locale_translations(is_admin),
            },
            "selected_model_id": selected_model_id,
            "available": {
                "models": bool(models),
                "prompts": bool(prompts),
                "tools": bool(tools),
                "knowledge": bool(knowledge),
            },
            "access": {"workspace": workspace},
            "resources": resources,
            "_enabled": sorted(enabled),
        }

    def _effective_features(self, permissions: dict, is_admin: bool, request) -> list[dict]:
        """A feature is shown only if it is ON for the platform AND allowed for this user."""
        features = permissions.get("features", {}) if isinstance(permissions, dict) else {}
        chat = permissions.get("chat", {}) if isinstance(permissions, dict) else {}
        config = getattr(getattr(getattr(request, "app", None), "state", None), "config", None)
        results = []
        for key, labels in FEATURE_DEFINITIONS.items():
            if not self._globally_enabled(config, key):
                continue
            source = chat if key in CHAT_PERMISSION_KEYS else features
            allowed = is_admin or (bool(source.get(key, False)) if isinstance(source, dict) else False)
            if allowed:
                results.append({"key": key, "enabled": True, "label": {"fr": labels[0], "en": labels[1]}})
        return results

    @staticmethod
    def _globally_enabled(config, key: str) -> bool:
        names = GLOBAL_FEATURE_FLAGS.get(key)
        if not names or config is None:
            return True
        for name in names:
            try:
                value = getattr(config, name)
            except Exception:
                continue
            value = getattr(value, "value", value)
            if isinstance(value, bool):
                return value
        return True

    async def _load_permissions(self, user) -> dict:
        from open_webui.models.config import Config
        from open_webui.utils.access_control import get_permissions

        defaults = await Config.get("user.permissions")
        return await get_permissions(str(user.id), defaults or {})

    async def _load_models(self, request, user) -> list[dict]:
        from open_webui.utils.models import get_all_models, get_filtered_models

        models = await get_all_models(request, refresh=False, user=user)
        models = [m for m in models if not (isinstance(m.get("pipeline"), dict) and m["pipeline"].get("type") == "filter")]
        models = list({str(m.get("id")): m for m in models if m.get("id")}.values())
        return await get_filtered_models(models, user)

    async def _load_prompts(self, user) -> list:
        from open_webui.config import BYPASS_ADMIN_ACCESS_CONTROL
        from open_webui.models.prompts import Prompts

        if getattr(user, "role", None) == "admin" and BYPASS_ADMIN_ACCESS_CONTROL:
            return await Prompts.get_prompts()
        return await Prompts.get_prompts_by_user_id(str(user.id), "read")

    async def _load_tools(self, request, user) -> list:
        from open_webui.routers.tools import get_tools

        return await get_tools(request=request, query=None, user=user, db=None)

    async def _load_knowledge(self, user) -> list:
        from open_webui.routers.knowledge import get_knowledge_bases

        result = await get_knowledge_bases(page=1, user=user, db=None)
        return list(getattr(result, "items", None) or self._value(result, "items", []) or [])

    def _named(self, item) -> dict:
        meta = self._value(item, "meta", {}) or {}
        return {
            "name": self._plain(self._value(item, "name", ""), 80),
            "description": self._plain(self._value(meta, "description", "") or self._value(item, "description", ""), 160),
        }

    def _unique_functions(self, models: list, key: str, limit: int) -> list[dict]:
        if not self.valves.expose_function_names:
            return []
        seen, out = set(), []
        for model in models:
            for fn in (model.get(key) or []) if isinstance(model, dict) else []:
                fid = str((fn or {}).get("id") or (fn or {}).get("name") or "")
                if not fid or fid in seen:
                    continue
                seen.add(fid)
                out.append({"name": self._plain(fn.get("name") or fid, 80), "description": self._plain(fn.get("description", ""), 160)})
                if len(out) >= limit:
                    return out
        return out

    def _language_for(self, user) -> str:
        if self.valves.use_user_interface_language:
            settings = self._settings_dict(user)
            language = self._normalize_locale(
                ((settings.get("ui") or {}) if isinstance(settings, dict) else {}).get("language")
            )
            if language in SUPPORTED_LOCALES:
                return language
            base = language.split("-", 1)[0]
            if base in SUPPORTED_LOCALES:
                return base
        fallback = self._normalize_locale(self.valves.default_language)
        if fallback in SUPPORTED_LOCALES:
            return fallback
        base = fallback.split("-", 1)[0]
        return base if base in SUPPORTED_LOCALES else "en"

    @staticmethod
    def _normalize_locale(value) -> str:
        return str(value or "").strip().lower().replace("_", "-")

    def _title(self, snapshot: dict) -> str:
        lang = (snapshot.get("ui") or {}).get("default_language", self.valves.default_language)
        template = self.valves.welcome_title_fr if lang == "fr" else self.valves.welcome_title_en
        title = template.replace("{emoji}", self.valves.title_emoji or "").replace("{product}", snapshot["brand"]["product"])
        return self._plain(title, 120) or "Welcome"

    def _render_html(self, snapshot: dict) -> str:
        public = {k: v for k, v in snapshot.items() if not k.startswith("_")}
        is_admin = bool(((public.get("ui") or {}) if isinstance(public, dict) else {}).get("is_admin"))
        localization = dict(public.get("localization") or {})
        localization["translations"] = self._locale_translations(is_admin)
        public["localization"] = localization
        payload = json.dumps(public, ensure_ascii=False, separators=(",", ":"))
        # Prevent script termination and HTML parser ambiguity inside the JSON script block.
        payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        template = ONBOARDING_HTML
        admin_start = "/*__ADMIN_ONLY_START__*/"
        admin_end = "/*__ADMIN_ONLY_END__*/"
        if is_admin:
            template = template.replace(admin_start, "").replace(admin_end, "")
        else:
            template = re.sub(
                rf"{re.escape(admin_start)}.*?{re.escape(admin_end)}",
                "",
                template,
                flags=re.DOTALL,
            )
        return template.replace("__SNAPSHOT_JSON__", payload)

    @staticmethod
    def _locale_translations(is_admin: bool) -> dict[str, dict[str, str]]:
        hidden = set() if is_admin else set(ADMIN_LOCALE_KEYS)
        return {
            locale: {key: value for key, value in translations.items() if key not in hidden}
            for locale, translations in EXTRA_LOCALE_TRANSLATIONS.items()
        }

    def _snapshot_hash(self, snapshot: dict) -> str:
        stable = {k: v for k, v in snapshot.items() if k not in {"generated_at"}}
        raw = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def _fallback_text(self, snapshot: dict) -> str:
        lang = (snapshot.get("ui") or {}).get("default_language", self.valves.default_language)
        return FALLBACK_TEXT_FR if lang == "fr" else FALLBACK_TEXT_EN

    # ================================================================ Testing
    async def _maybe_run_test(self, app, request) -> None:
        target = (self.valves.test_user or "").strip()
        revision = int(self.valves.test_revision or 0)
        if not target or revision <= 0:
            return
        user_id = await self._resolve_user(target)
        if not user_id:
            log.warning("[onboarding] test user not found target=%s", target)
            return
        from open_webui.models.users import Users

        user = await Users.get_user_by_id(user_id)
        state = self._settings_dict(user).get(TEST_SETTINGS_KEY) or {}
        if isinstance(state, dict) and revision <= int(state.get("revision", 0) or 0):
            return
        if self.valves.test_assign_group:
            await self._assign_group(user_id)
        result = await self._ensure_guide(user_id, app, request, source=f"test:{revision}", allow_create=True, force=True)
        await self._update_user_settings(user_id, {TEST_SETTINGS_KEY: {"revision": revision, "run_at": int(time.time()), "result": result}})

    # ============================================================== Storage
    @staticmethod
    def _marker_valid(marker: Optional[dict]) -> bool:
        return bool(marker) and int(marker.get("version", 0)) >= ONBOARDING_VERSION and bool(marker.get("chat_id")) and bool(marker.get("message_id"))

    async def _get_marker(self, user_id: str) -> Optional[dict]:
        try:
            from open_webui.models.users import Users

            user = await Users.get_user_by_id(user_id)
            marker = self._settings_dict(user).get(SETTINGS_KEY)
            return dict(marker) if isinstance(marker, dict) else None
        except Exception:
            log.warning("[onboarding] marker read failed user=%s", user_id, exc_info=True)
            return None

    async def _save_marker(self, user_id: str, marker: dict) -> None:
        await self._update_user_settings(user_id, {SETTINGS_KEY: marker})

    async def _assign_group(self, user_id: str) -> None:
        group_id = (self.valves.default_group_id or "").strip()
        if not group_id:
            return
        try:
            from open_webui.models.groups import Groups

            if await Groups.add_users_to_group(group_id, [user_id]) is None:
                log.warning("[onboarding] default group not found id=%s", group_id)
        except Exception:
            log.exception("[onboarding] default group assignment failed user=%s", user_id)

    def _redis_call(self, action, what: str):
        """Run a Redis command, reconnecting once if the connection was dropped.

        Idle connections are closed by Redis or by the proxy in front of it, so the
        first command after a quiet period can fail with a broken pipe. That is
        routine, not an error: we reconnect and retry, and only fall back to
        process-local locking if the second attempt also fails.
        """
        if self._redis is None:
            return None, False
        for attempt in (1, 2):
            try:
                return action(self._redis), True
            except Exception as exc:
                if attempt == 1:
                    log.info("[onboarding] redis %s retry after %s", what, type(exc).__name__)
                    self._redis = self._connect_redis()
                    if self._redis is None:
                        return None, False
                    continue
                log.warning("[onboarding] redis %s unavailable (%s), using process-local locking", what, type(exc).__name__)
                return None, False
        return None, False

    def _acquire_key(self, key: str, ttl: int) -> Optional[str]:
        token = str(uuid.uuid4())
        result, ok = self._redis_call(lambda r: r.set(key, token, nx=True, ex=ttl), "lock")
        if ok:
            return token if result else None
        if key in self._local_locks:
            return None
        self._local_locks.add(key)
        return token

    def _release_key(self, key: str, token: str) -> None:
        self._redis_call(
            lambda r: r.eval(
                "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
                1, key, token,
            ),
            "unlock",
        )
        self._local_locks.discard(key)

    def _lock_key(self, user_id: str) -> str:
        return f"{self._prefix}:secure-onboarding:lock:user:{user_id}"

    # ============================================================== Helpers
    @staticmethod
    async def _maybe_await(value):
        if asyncio.iscoroutine(value) or isinstance(value, asyncio.Future):
            return await value
        return value

    @staticmethod
    def _user_id_from_event(event_name: str, event: dict) -> Optional[str]:
        actor = event.get("actor") if isinstance(event, dict) else None
        if event_name in ("auth.signup", "auth.login") and isinstance(actor, dict) and actor.get("id"):
            return str(actor["id"])
        for key in ("subject", "data", "user", "object", "actor"):
            value = event.get(key) if isinstance(event, dict) else None
            if isinstance(value, dict) and value.get("id"):
                return str(value["id"])
        return None

    @staticmethod
    async def _resolve_user(value: str) -> Optional[str]:
        from open_webui.models.users import Users

        user = await Users.get_user_by_email(value) if "@" in value else await Users.get_user_by_id(value)
        return str(getattr(user, "id")) if user and getattr(user, "id", None) else None

    @staticmethod
    async def _update_user_settings(user_id: str, patch: dict) -> None:
        from open_webui.models.users import Users

        if await Users.update_user_settings_by_id(user_id, patch) is None:
            raise RuntimeError("User settings update failed")

    @staticmethod
    def _settings_dict(user) -> dict:
        if user is None:
            return {}
        settings = getattr(user, "settings", None)
        if isinstance(settings, dict):
            return dict(settings)
        if hasattr(settings, "model_dump"):
            try:
                return settings.model_dump()
            except Exception:
                return {}
        return {}

    @staticmethod
    def _value(value: Any, key: str, default=None):
        if isinstance(value, dict):
            return value.get(key, default)
        return getattr(value, key, default)

    @staticmethod
    def _plain(value: Any, limit: int) -> str:
        text = html.unescape(str(value or ""))
        text = re.sub(r"[\x00-\x1f\x7f]+", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[: int(limit)]

    @staticmethod
    def _safe_color(value: str, fallback: str) -> str:
        candidate = (value or "").strip().upper()
        return candidate if re.fullmatch(r"#[0-9A-F]{6}", candidate) else fallback

    @staticmethod
    def _safe_http_url(value: str) -> str:
        candidate = (value or "").strip()
        if not candidate:
            return ""
        try:
            parsed = urlparse(candidate)
            return candidate if parsed.scheme == "https" and parsed.netloc and not parsed.username and not parsed.password else ""
        except Exception:
            return ""

    @staticmethod
    def _usable_request(app, request):
        if request is not None and getattr(request, "app", None) is not None:
            return request
        if app is None:
            raise RuntimeError("Open WebUI application context is required to build the access catalog")
        from starlette.requests import Request

        return Request({
            "type": "http", "http_version": "1.1", "method": "GET", "scheme": "https",
            "path": "/", "raw_path": b"/", "query_string": b"", "headers": [],
            "client": ("127.0.0.1", 0), "server": ("127.0.0.1", 443), "app": app,
        })

    @staticmethod
    def _connect_redis():
        try:
            from open_webui.env import REDIS_URL
            from open_webui.utils.redis import get_redis_connection

            if REDIS_URL:
                try:
                    # Ping idle connections before use so a dropped socket is
                    # reopened instead of raising on the next command.
                    return get_redis_connection(REDIS_URL, decode_responses=True, health_check_interval=30)
                except TypeError:
                    return get_redis_connection(REDIS_URL, decode_responses=True)
        except Exception:
            log.info("[onboarding] redis unavailable, using process-local locking")
        return None

    @staticmethod
    def _redis_prefix() -> str:
        try:
            from open_webui.env import REDIS_KEY_PREFIX

            return REDIS_KEY_PREFIX or "open-webui"
        except Exception:
            return "open-webui"
