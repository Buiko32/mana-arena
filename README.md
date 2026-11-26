# Mana Arena

Mana Arena gestisce tornei amatoriali di Magic in italiano. È pensata per una partita 1v1, con un massimo di 64 partecipanti. Gli abbinamenti e gli spareggi sono strumenti amatoriali: l’OMW usa la percentuale effettiva senza soglia minima. L’app non gestisce tornei multiplayer Commander e non offre funzioni o certificazioni DCI/sanzionate.

## Versione

La versione mostrata nel footer è `0.01.50` e segue il formato `major.release.fix`: il primo numero indica la major release, il secondo la release di base e l’ultimo i fix. Per ogni modifica richiesta dall’utente si incrementa il numero dei fix; release di base e major avanzano quando viene richiesta una nuova release di quel livello. Aggiorna il valore nel footer di `app/static/index.html` insieme alla modifica.

## Scelta del giocatore

Alla prima apertura il browser chiede di scegliere un giocatore. La scelta è ricordata sul dispositivo e può essere cambiata dal menu in alto. Si può continuare senza scegliere, ad esempio per accedere all’area organizzatore quando non ci sono ancora giocatori. Il profilo scelto non costituisce autenticazione.

## Scoprire il proprio abbinamento

Apri un torneo pubblico e scegli **Scopri il tuo incontro**. Seleziona il tuo nome e il turno: una breve animazione mostra l'avversario e il tavolo, oppure segnala bye, incontro annullato o abbinamento non ancora disponibile. I dati vengono riletti dal server prima della rivelazione. Gli incontri restano pubblici anche nelle viste turni e tabellone; non è una funzione di segretezza o autenticazione. Con movimento ridotto il risultato compare subito.

## Amichevoli a squadre

Da **Inserisci risultato** si può registrare un’amichevole da 1 a 3 giocatori per parte, anche 1 contro 1. Il riepilogo mostra le due squadre e il vincitore prima del salvataggio. L’Elo di ciascun giocatore viene confrontato con la media Elo della squadra avversaria prima della partita. Per ogni avversario in più si aggiungono 150 punti alla forza avversaria stimata; per ogni compagno in più se ne sottraggono 150. La variazione usa il K personale e considera il risultato della squadra: le variazioni individuali possono essere diverse e la somma dei punti può cambiare. Il vantaggio numerico è una convenzione regolabile, non una misura verificata del gioco.

## Classifica globale e reset

La classifica globale ricalcola gli incontri ancora validi di tutti i tornei, compresi quelli archiviati, in ordine di registrazione dei risultati. Per i vecchi dati senza un evento in cronologia usa un ordine deterministico basato sul torneo e sul turno. Ogni profilo parte da 1500 punti: K=40 per le prime 30 partite valide, poi K=20, oppure K=10 dopo aver raggiunto 2400; patte storiche, bye, forfait e incontri annullati non vengono conteggiati. La probabilità usa la formula Elo con differenza limitata a 400 punti per rating inferiori a 2650. È una classifica interna ispirata alle soglie FIDE: non usa i periodi ufficiali, le regole per minorenni o l'inizializzazione FIDE e non è un rating ufficiale. Correzioni e rollback ricalcolano la classifica dai risultati esistenti. Nella bozza un organizzatore può applicare i seed globali e poi modificarli manualmente.

Il profilo scelto nella pagina pubblica è ricordato solo nel browser e non autentica il giocatore. In **Organizza → Backup** si può scegliere tra eliminare turni e risultati riportando i tornei in bozza, eliminare i tornei conservando i profili, o eliminare tutti i dati di gioco e le foto. Ogni azione richiede di digitare ELIMINA; l'account amministratore resta. Scaricare prima un backup: le eliminazioni sono irreversibili. Se una foto non può essere rimossa dal disco, l'app segnala il numero di file rimasti.

## Installazione su UGREEN UGOS

Serve Docker/Container Manager e accesso in scrittura alla cartella del progetto. Crea `.env` copiando `.env.example` e sostituisci i tre valori di esempio. Genera tre valori distinti e casuali, ad esempio con Python:

```text
python -c "import secrets; print(secrets.token_urlsafe(24))"
python -c "import secrets; print(secrets.token_urlsafe(24))"
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Usa i tre valori per `SHARED_ACCESS_PASSWORD`, `ADMIN_PASSWORD` e `SESSION_SECRET`, rispettivamente; copia i risultati nel file senza incollarli in chat o in un repository. Il nome admin iniziale è `organizzatore` e può essere cambiato prima del primo avvio. Il segnaposto `change-me` viene rifiutato all’inizializzazione.

In UGOS: apri **App Center → Docker → Project → Create**, scegli la cartella che contiene `docker-compose.yaml` e `.env`, quindi distribuisci il progetto. La lettura automatica di `.env` dall’interfaccia Project non è stata verificata; se l’interfaccia non carica l’ambiente, usa il terminale del NAS nella cartella del progetto:

```sh
docker compose --env-file .env -f docker-compose.yaml pull
docker compose --env-file .env -f docker-compose.yaml up -d
```

Compose scarica `ghcr.io/buiko32/mana-arena:latest`, pubblica la porta `3388` e conserva database e foto in due volumi Docker nominati (`mana-arena-db` e `mana-arena-photos`; Docker può anteporre il nome del progetto). Attendi lo stato `healthy`, poi apri il sito attraverso il reverse proxy HTTPS. `/healthz` indica se l’app e il database sono pronti. Le viste pubbliche sono in sola lettura; l’accesso admin non ha registrazione pubblica.

Condividi con i partecipanti solo `SHARED_ACCESS_PASSWORD` (almeno 12 caratteri). Al primo accesso ciascun browser riceve un cookie valido per 90 giorni. In **Organizza** puoi vedere e revocare i browser registrati. Chi conosce la password condivisa può registrarsi di nuovo. Per scollegare tutti, cambia `SHARED_ACCESS_PASSWORD` nel file `.env` e ricrea il container. Il registro viene conservato nel volume database, separatamente dai backup dei tornei. Un browser già autorizzato può conservare sul dispositivo i dati del segnavita già scaricati per l’uso offline.

Per l’accesso da Internet configura un reverse proxy HTTPS e imposta `COOKIE_SECURE=true`. Per una prova locale su HTTP è necessario `COOKIE_SECURE=false`.

Per aggiornare dopo una pubblicazione:

```sh
docker compose --env-file .env -f docker-compose.yaml pull
docker compose --env-file .env -f docker-compose.yaml up -d
```

Il progetto usa UID/GID `10001` nel container. I volumi nominati vengono inizializzati con i permessi necessari. Se preferisci cartelle del NAS al posto dei volumi, sostituisci i mount con percorsi bind e rendi scrivibili `/db` e `/photos` all’UID/GID `10001`; non usare permessi `777`.

## Backup e ripristino

Il comando **Backup e ripristino** nell’area admin scarica un archivio ZIP coerente con database e foto. Conservalo fuori dal NAS e ripristinalo dall’app: il server verifica archivio, database e immagini e revoca le sessioni dopo il ripristino. Questo è il metodo consigliato per spostare o recuperare i dati. Per backup a livello Docker, arresta il progetto e salva insieme entrambi i volumi `mana-arena-db` e `mana-arena-photos`; ripristina entrambi prima di riavviare, perché il database contiene i riferimenti alle foto.

Le esportazioni CSV sono disponibili per classifiche, iscritti e incontri nell’area admin. Il CSV non sostituisce il backup completo.

## Verifiche per lo sviluppo

Con Python 3.12 e Node.js installati, esegui:

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
node --version
node --check app/static/brackets.js
node --check app/static/app.js
node --test tests/brackets.test.cjs tests/reveal.test.cjs tests/identity-welcome.test.cjs
```

GitHub Actions esegue gli stessi test prima di costruire l’immagine.

## Pubblicazione e limiti

GitHub Actions esegue i test, costruisce e prova l’immagine `linux/amd64`, poi pubblica su GHCR i tag `latest` e commit; i tag di release `v*` ricevono anche il tag di versione. Alla prima pubblicazione, rendi il package GHCR **Public** dalle impostazioni del package: la visibilità del repository non lo rende pubblico automaticamente. La modalità manuale `verify_public_only` prova il pull anonimo e l’avvio dell’immagine pubblica esistente senza login al registry.

La distribuzione sul NAS UGREEN non è stata eseguita né verificata. Il target del container è Linux amd64. Le regole di torneo sono amatoriali e non sostituiscono un software ufficiale di organizzazione o arbitraggio.

### Statistiche del contatore vita

Le partite concluse vengono archiviate separatamente in `life-stats.sqlite3`, con vincitore, vita iniziale e finale, cronologia delle variazioni e riepiloghi. In `.env` imposta `LIFE_STATS_HOST_PATH` sul percorso della cartella del NAS (predefinito `./life-stats`), Compose la monta in `/data/life`. All’avvio il servizio monouso `prepara-statistiche` controlla l’accesso dell’UID 10001 e prepara i permessi della cartella dedicata e degli eventuali file SQLite. I log del servizio sono consultabili nell’interfaccia Docker del NAS. Per avvii senza Compose si può impostare `LIFE_STATS_DIR` direttamente; altrimenti si usa `DATA_DIR/life`. Le richieste ripetute della stessa sessione non creano duplicati.

Le statistiche vita **non sono incluse nel backup ZIP dell'app**: arresta il servizio e copia separatamente la cartella configurata, insieme ai backup di database e foto. La registrazione è pubblica come il contatore: usa l'app su una rete fidata. I dati possono contenere i nomi dei giocatori.

Se il salvataggio segnavita restituisce **503**, la risposta indica quando possibile se il volume è in sola lettura, mancano permessi, è esaurito lo spazio, il database è occupato oppure il percorso non è apribile. I log del servizio riportano il percorso nel container e i codici dell'errore, senza dati della partita. Dalla cartella Compose puoi controllare:

```sh
docker compose config
docker compose logs --tail=100 mana-arena
docker compose exec mana-arena id
docker compose exec mana-arena ls -ld /data/life
docker compose exec mana-arena df -h /data/life
```

Nel NAS di questa installazione il percorso previsto è `/volume2/dockerssd/mana-arena/stats`, montato come `/data/life` nel container: verifica che `LIFE_STATS_HOST_PATH` punti a quella cartella e che il mount effettivo corrisponda alla configurazione. Il processo gira come UID/GID `10001`: l'utente deve poter attraversare le cartelle genitrici e leggere, creare e modificare file nella cartella e nell'eventuale database già esistente. Verifica anche permessi ACL, spazio e quota sul NAS, e che il mount non sia in sola lettura. Correggi i permessi della sola cartella interessata secondo la configurazione del NAS, senza usare `777`. Dopo una modifica del bind o delle variabili ricrea il container con Compose; dopo la correzione ritenta il salvataggio. Un percorso vuoto nel container non prova da solo che sia collegato al NAS: verifica esplicitamente il mount effettivo. Non viene utilizzata una cartella alternativa in caso di errore.

Per leggere l'archivio con SQLite, apri il file `life-stats.sqlite3` con un programma come DB Browser for SQLite oppure esegui `SELECT session_id, ended_at, payload FROM games;`. Il campo JSON `payload` contiene `format`, i lati `players` con i singoli `members` (ID profilo e nome), `changes`, `winner` e `statistics`.

### Segnavita al tavolo

Apri **Segnavita** dalla barra in basso e conserva il collegamento alla pagina `/segnavita` sul telefono. Per poterla riaprire senza rete, accedi una prima volta in HTTPS e lascia completare il caricamento. Il browser conserva sul telefono i file necessari, i profili disponibili e la partita in corso. In modalità privata o cancellando i dati del sito puoi perdere le partite ancora da inviare.

I tocchi sui contatori e i lanci del dado avvengono sul telefono. Alla fine scegli **Termina**, verifica il vincitore e seleziona **Salva risultato**. Se il NAS è irraggiungibile, riapri il segnavita con lo stesso browser quando torna la connessione per sincronizzare le partite concluse. Per una partita di torneo offline serve l’abbinamento già caricato sul dispositivo. Se nel frattempo il risultato è stato registrato, l’archivio conserva i dati vita e l’app segnala il conflitto.

Mentre il tavolo è visibile, il segnavita chiede al telefono di mantenere il display acceso. Su un NAS aperto dal telefono tramite indirizzo IP in HTTP, la riapertura offline e il mantenimento del display acceso richiedono l’accesso in HTTPS.
