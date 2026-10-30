# Mana Arena — design approvato

**Stato:** approvato il 30 ottobre 2026; implementazione non ancora avviata.  
**Destinazione prevista:** NAS UGREEN DXP2800 (Intel N100), tramite Docker.  
**Nome:** Mana Arena.

## Intento

Realizzare un’applicazione web privata, in italiano e adatta a telefono e desktop, per organizzare tornei di Magic: The Gathering in casa o in un gruppo. Un amministratore gestisce giocatori e tornei; chiunque abbia accesso alla pagina può consultare i dati senza creare un account. Il servizio dovrà funzionare sul NAS con manutenzione semplice e dati persistenti.

Il workbook `Torneo_Magic_6_Giocatori_BO1_Doppia_Sconfitta.xlsx` è un riferimento di dominio: descrive sei giocatori, BO1, svizzera adattata e uscita dopo due sconfitte. Il preset “Come Excel” include quel formato, senza importare i dati dimostrativi né promettere di riprodurre integralmente il workbook.

## Obiettivi

- Creare e gestire più tornei, mantenendoli consultabili nel tempo.
- Riutilizzare un elenco condiviso di giocatori; l’amministratore può aggiungere, modificare e rimuovere i profili e caricare foto.
- Scegliere separatamente formato di torneo, formato di gioco Magic e lunghezza dell’incontro.
- Gestire iscrizioni, teste di serie, sorteggio, ritiri, turni, abbinamenti, bye, risultati e classifiche.
- Rendere pubblica in sola lettura la consultazione di tornei, partecipanti, turni e classifiche.
- Conservare dati e foto al riavvio del container e documentare backup e ripristino.
- Pubblicare i sorgenti nel repository GitHub pubblico `Buiko32/mana-arena`.
- Distribuire l’immagine Linux amd64 pubblica `ghcr.io/buiko32/mana-arena:latest`, scaricata da GitHub Container Registry tramite Compose.

## Non obiettivi

- Gestire incontri multiplayer di Commander. Per questa versione ogni incontro è 1 contro 1; il Commander multiplayer è fuori scope.
- Fornire account pubblici, registrazione degli utenti o permessi diversi per più amministratori.
- Garantire conformità a sanzionamento, pairing, classifiche o regolamenti ufficiali di eventi competitivi.
- Importare automaticamente l’intero workbook o promettere compatibilità completa con Excel.
- Configurare il router, pubblicare il servizio su Internet o gestire il reverse proxy/HTTPS del NAS.

## Approcci considerati

1. **Servizio unico FastAPI e SQLite — raccomandato.** Un solo processo applicativo contiene API e interfaccia web e usa SQLite per i dati. Riduce componenti, aggiornamenti e manutenzione sul NAS; con un solo amministratore e tornei di dimensioni contenute è una soluzione proporzionata. Richiede di serializzare le scritture e fare backup coerenti.
2. **Frontend e API separati con PostgreSQL.** Offre una separazione più netta e più margine per accessi concorrenti o più servizi, ma introduce container, configurazione e manutenzione aggiuntivi senza un beneficio atteso per l’uso descritto.
3. **Gestione basata su fogli di calcolo.** Riutilizza un flusso familiare, ma rende più difficile consultare tornei multipli da mobile, limitare le modifiche amministrative, mantenere dati condivisi e recuperare in modo controllato modifiche ai turni.

La proposta è l’approccio 1. Stack: Python, FastAPI, SQLite e interfaccia responsive HTML/CSS/JavaScript servita dalla stessa applicazione. Un singolo processo applica transazioni alle operazioni che modificano torneo, risultati e abbinamenti.

## Modello dei tornei

Ogni torneo conserva configurazione, stato, giocatori iscritti, turni, incontri, risultati e registro delle modifiche. Il limite iniziale è **da 2 a 64 giocatori per torneo**. Le impostazioni del torneo sono personalizzabili in bozza e vengono mostrate nel riepilogo prima dell’avvio.

I tre assi di configurazione sono indipendenti:

- **Struttura del torneo:** svizzera classica, svizzera adattata a due sconfitte, campionato all’italiana (sola andata o andata e ritorno), eliminazione diretta.
- **Formato Magic:** etichetta selezionata da Standard, Modern, Commander, Pauper o “Altro”, con nome libero per quest’ultima. Commander è conservato come etichetta del formato di gioco, ma gli incontri restano 1 contro 1.
- **Durata dell’incontro:** BO1 o BO3. La durata descrive l’incontro; la struttura governa pairing ed eliminazioni.

Stati di un torneo: **bozza**, **attivo**, **concluso**, **archiviato**. In bozza si configurano torneo, iscrizioni e seed; l’avvio congela la configurazione usata. I tornei attivi non permettono di cambiare le regole che influenzano risultati, pairing o classifica. Per correggere retroattivamente una configurazione dopo l’avvio è necessario annullare esplicitamente tutti i turni e i risultati dipendenti tramite rollback completo; il sistema mostra l’impatto e chiede conferma. Concluso indica che il torneo ha un esito finale; archiviato lo nasconde dalle viste operative senza cancellarlo. Le modifiche a un torneo concluso o archiviato richiedono prima una riapertura esplicita e il rollback completo necessario.

### Impostazioni personalizzabili

Per ogni torneo l’amministratore può configurare in bozza:

- nome, data, descrizione e struttura;
- formato Magic (Standard, Modern, Commander, Pauper o etichetta libera) e incontro BO1/BO3;
- numero di turni svizzeri da 1 a 32; la proposta precompila `ceil(log2(numero di partecipanti))`;
- soglia di sconfitte della svizzera adattata da 2 a 5; il preset **“Come Excel”** preimposta 2;
- punti per vittoria, patta, sconfitta e bye, ciascuno da 0 a 10; vittoria deve valere più della sconfitta e, se la patta è ammessa, il relativo punteggio deve stare fra sconfitta e vittoria. Le patte sono abilitate solo nei formati che le ammettono; il preset **“Come Excel”** usa BO1, vittoria 1, patta non ammessa, sconfitta 0, bye 1;
- sola andata o andata e ritorno per il campionato;
- seeding manuale o casuale;
- ordine degli spareggi, scelto fra OMW (percentuale punti avversari), vittorie, differenza game vinti/persi e seed; il seed è sempre il criterio finale di ordinamento.

La svizzera adattata accetta BO1 o BO3; il preset “Come Excel” seleziona BO1. Qualunque sia la durata, il risultato di incontro per questo formato deve determinare un vincitore e aumentare le sconfitte del perdente. Le opzioni incompatibili vengono disabilitate o rifiutate prima dell’avvio, con spiegazione visibile. La configurazione effettiva, inclusi valori e ordine degli spareggi, viene salvata con il torneo e non può essere modificata silenziosamente dopo l’avvio.

Per il seeding casuale, il server genera e registra un seed casuale al momento della preparazione; l’ordine sorteggiato è così riproducibile e verificabile nel riepilogo e nell’audit log.

### Regole proposte per struttura

- **Svizzera classica:** nessuna eliminazione automatica per sconfitte; pairing deterministico vicino per punti e senza rivincite quando possibile. Se una rivincita è inevitabile, viene segnalata. Numero di turni personalizzabile da 1 a 32; suggerimento iniziale `ceil(log2(numero di partecipanti))`. Punteggi e spareggi sono configurabili. OMW è la media delle percentuali punti degli avversari effettivamente affrontati; bye e forfait non sono incontri giocati. In assenza di avversari o incontri dell’avversario, la percentuale è 0. L’ordine selezionato fra OMW, vittorie, game difference è seguito sempre dal seed come criterio finale. Nessun taglio automatico a eliminazione diretta.
- **Svizzera adattata:** la soglia di sconfitte è personalizzabile da 2 a 5; il preset “Come Excel” usa due. Il pairing favorisce il gruppo con lo stesso numero di sconfitte e riduce le rivincite quando possibile. Durata BO1 o BO3, con BO1 come predefinito del preset. I punti vittoria/sconfitta/bye e l’eventuale patta compatibile sono configurabili da 0 a 10; il preset “Come Excel” è vittoria 1, sconfitta 0, bye 1, nessuna patta. Un bye non è vittoria giocata né sconfitta. Raggiunta la soglia, il giocatore è escluso dai pairing successivi. La finale resta soggetta alla medesima soglia e può richiedere una rivincita; il torneo termina quando resta un solo giocatore non eliminato. Se manca un pairing legale il turno resta in attesa e l’amministratore deve risolverlo; non si assegna una vittoria automatica.
- **Campionato all’italiana:** calendario round-robin; sola andata o andata e ritorno configurabile. Punti configurabili, patte compatibili con la struttura. Classifica per punti, poi spareggi nell’ordine selezionato e seed finale. In BO1 ogni incontro giocato vale un game; per BO3 si registrano game vinti e persi. Con partecipanti dispari il bye non dà punti.
- **Eliminazione diretta:** tabellone per seed o sorteggio configurabile; bye iniziali assegnati ai seed più alti oppure casualmente secondo la configurazione. Ogni incontro richiede un vincitore. Una patta non conclude l’incontro; il risultato va registrato quando è stato determinato il vincitore. Il perdente è eliminato.

Il sistema valida la compatibilità fra struttura e risultato. Nei formati che ammettono patte l’amministratore può registrare vittoria, sconfitta o patta; in quelli che richiedono un vincitore la patta non è un risultato conclusivo. Le regole qui indicate sono impostazioni proposte per tornei amatoriali, non certificazione delle regole ufficiali di Magic.

### Abbinamenti e risultati

Gli abbinamenti svizzeri sono calcolati in modo deterministico, a partire da punti, sconfitte, seed e storico degli incontri. L’algoritmo cerca prima accoppiamenti legali senza rivincite e con distanza di punteggio contenuta; rilassa questi criteri in ordine dichiarato solo quando non trova una soluzione completa e segnala il compromesso. Ogni bye è assegnato a un partecipante eleggibile che ne ha ricevuti meno; se il criterio non può essere soddisfatto, la scelta viene resa visibile all’amministratore. L’interfaccia non presenta il risultato come pairing ufficiale.

Il ritiro è proposto come irreversibile, salvo rollback esplicito: se avviene durante un incontro ancora aperto, assegna la vittoria per forfait all’avversario; il ritirato è escluso dagli abbinamenti successivi. Per un bye già assegnato o un incontro già concluso non modifica automaticamente il risultato. Il forfait assegna i punti di vittoria previsti ma non conta come vittoria giocata né come game giocato. Nel campionato gli incontri futuri del ritirato sono registrati come forfait, senza creare game giocati. Se un doppio ritiro lascia zero partecipanti attivi, il torneo si conclude senza vincitore e con il motivo registrato. Nell’eliminazione diretta, se un giocatore si ritira fra turni, alla creazione del turno successivo l’avversario previsto nel tabellone avanza per forfait; se entrambi si sono ritirati, uno slot vuoto viene propagato. Se non restano superstiti, il torneo si conclude senza vincitore e con il motivo registrato.

Gli esiti sono immutabili rispetto ai turni successivi per impostazione operativa: correggere un risultato passato dopo aver creato turni seguenti mostra un riepilogo degli incontri e dei turni che saranno invalidati e richiede conferma esplicita. Dopo la conferma, il sistema annulla i risultati e gli abbinamenti dipendenti, registra l’operazione nel registro di audit e rigenera i turni a partire dal primo turno coinvolto. Non è prevista una modifica silenziosa a catena.

## Interfaccia e flussi

- **Aspetto proposto:** tema scuro con accenti ambra, schede torneo, schede turni/classifica/partecipanti, tabellone per eliminazione e microanimazioni che rispettano la riduzione del movimento. Sono scelte modificabili.
- **Vista pubblica:** elenco tornei attivi e conclusi, pagina torneo, partecipanti, turni, risultati e classifica. Nessun login o dato di contatto personale dei giocatori è esposto oltre a nome e foto scelti dall’amministratore.
- **Area amministrativa:** accesso protetto per gestire giocatori, foto, configurazione torneo, iscrizioni e seed, avvio, pairing, inserimento/correzione risultati, ritiri, chiusura, archiviazione ed esportazione.
- **Avvio torneo:** configurazione → iscrizioni → ordinamento/seeding o sorteggio → riepilogo regole → conferma avvio.
- **Operazioni del turno:** l’amministratore genera/accetta gli abbinamenti, registra bye o ritiri applicabili, inserisce risultati e conferma il completamento del turno. Il turno successivo viene proposto solo dopo il completamento richiesto dalle regole.
- **Aggiornamento ospiti:** polling leggero delle viste pubbliche, proposto ogni 5 secondi mentre la pagina è visibile; nessuna connessione push necessaria per la prima versione.
- **Accessibilità:** navigazione da tastiera, etichette per controlli, contrasto leggibile, stati non comunicati solo col colore e supporto a `prefers-reduced-motion`. Animazioni brevi e non necessarie alla comprensione.
- **Mobile:** layout utilizzabile su schermi stretti, azioni di inserimento risultati facilmente raggiungibili e tabelle trasformate in schede quando necessario.

## Amministrazione e sicurezza

È previsto un solo amministratore, senza registrazione pubblica. La prima credenziale viene fornita tramite variabili d’ambiente al primo avvio; la password è memorizzata solo come hash con algoritmo adatto alle password. Un successivo cambio credenziale avviene nell’area amministrativa. L’app rifiuta l’avvio se manca una configurazione iniziale valida invece di creare una password predefinita.

Le sessioni usano cookie `HttpOnly`, `SameSite` appropriato e `Secure` quando il servizio è dietro HTTPS. Le richieste che modificano dati sono protette da token CSRF. L’accesso amministrativo applica limite ai tentativi e ritardo progressivo. Le pagine pubbliche sono in sola lettura; gli endpoint di modifica verificano sempre la sessione sul server. I segreti restano fuori dal repository e dall’immagine Docker; `.env.example` contiene solo nomi di variabili e valori segnaposto non utilizzabili.

La porta applicativa esposta dal container è la **3388**. La mappatura della porta host e l’eventuale accesso da Internet sono decisioni di configurazione NAS; il progetto non apre porte del router. Per accesso esterno si presume un reverse proxy HTTPS configurato e amministrato fuori dall’app. La porta HTTP diretta è pensata per la rete locale fidata; cookie e impostazioni di produzione devono essere configurabili per la terminazione TLS esterna.

## Dati, file e persistenza

SQLite conserva amministratore, giocatori, tornei, iscrizioni, seed, turni, incontri, risultati, stato di torneo e audit log. Le foto sono file locali referenziati dal database; gli upload hanno limiti di dimensione e formati consentiti, nome file generato dal server e controlli sul contenuto. Il database non memorizza percorsi arbitrari forniti dall’utente.

Il container è Linux **amd64** e usa volumi persistenti separati: `/data/db` per il database e `/data/photos` per le foto. Il servizio non dipende da una connessione Internet per operare dopo l’avvio. Le scritture su torneo e risultati usano transazioni SQLite; il processo serializza le mutazioni e imposta timeout/locking per gestire richieste concorrenti senza corrompere i dati.

L’export CSV è disponibile per iscritti, turni/abbinamenti, risultati e classifica. Il backup deve includere database e directory foto in un unico archivio coerente: l’applicazione sospende brevemente le modifiche, crea una snapshot consistente del database con l’API di backup SQLite, copia le foto corrispondenti e produce un archivio con manifest/versione. Il ripristino è un’operazione amministrativa documentata, con conferma e verifica prima di sostituire i dati in uso. Copiare separatamente il solo file SQLite mentre l’app è attiva non è considerato un backup valido.

## Distribuzione e repository

Il repository pubblico è `Buiko32/mana-arena`. Include sorgenti, test automatici, documentazione, Dockerfile, Compose, health check e `.env.example`; non include credenziali, database reali o foto dei giocatori. GitHub Actions esegue test e build multipiattaforma limitata a Linux amd64, poi pubblica l’immagine pubblica `ghcr.io/buiko32/mana-arena:latest` su GitHub Container Registry. Compose usa direttamente l’immagine pubblicata (`image:`) e non contiene una build locale (`build:`), così il NAS la scarica da GHCR.

La configurazione Compose espone la porta host 3388 verso la porta container 3388 e monta i due volumi persistenti. L’endpoint `/healthz` verifica che il processo risponda e che il database sia apribile, senza esporre segreti. Il NAS non è accessibile nell’ambiente di sviluppo: l’installazione effettiva sul DXP2800 resta una verifica successiva, dopo che l’immagine è pubblicata.

## Verifica proposta

- Test unitari delle regole per tutte le strutture: torneo vuoto/minimo, numero pari e dispari, bye ripetuti, ritiri, pareggi ammessi e vietati, ultima sconfitta, completamento e spareggi.
- Test dei pairing: determinismo, evitare rivincite quando possibile, gruppi di punteggio/sconfitte, rilassamenti espliciti e caso senza pairing legale.
- Test di sicurezza: pagine pubbliche in sola lettura, richieste amministrative senza sessione, CSRF, limitazione tentativi e validazione upload.
- Test di persistenza: transazioni, riavvio con recupero dei dati, modifica retroattiva con invalidazione e audit, backup e ripristino di database più foto.
- Test dell’interfaccia per layout mobile, flusso amministrativo, accessibilità di base e aggiornamento delle viste pubbliche.
- Build e health check Docker per amd64 quando l’ambiente è disponibile. In questa fase non risulta disponibile Docker locale e il NAS non è accessibile: non si dichiara verificata l’esecuzione sul dispositivo.

## Approvazione del design

Il design è stato approvato il 30 ottobre 2026 con due modifiche: le impostazioni del singolo torneo sono personalizzabili come descritto sopra; il repository GitHub è pubblico e pubblica l’immagine su GHCR, scaricata dal NAS tramite Compose. Repository e package: `Buiko32/mana-arena` e `ghcr.io/buiko32/mana-arena:latest`. Le regole e i valori iniziali restano preset modificabili in bozza, poi bloccati all’avvio salvo rollback completo esplicito.

## Confini per chi implementerà

Dopo approvazione del design, l’implementazione segue il piano in `docs/superpowers/plans/2026-10-30-mana-arena.md`. Il workbook resta un riferimento e non implica import automatico o compatibilità completa.
