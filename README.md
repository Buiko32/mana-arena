# Mana Arena

Mana Arena gestisce tornei amatoriali di Magic in italiano. È pensata per una partita 1v1, con un massimo di 64 partecipanti. Gli abbinamenti e gli spareggi sono strumenti amatoriali: l’OMW usa la percentuale effettiva senza soglia minima. L’app non gestisce tornei multiplayer Commander e non offre funzioni o certificazioni DCI/sanzionate.

## Installazione su UGREEN UGOS

Serve Docker/Container Manager e accesso in scrittura alla cartella del progetto. Crea `.env` copiando `.env.example` e sostituisci i due valori di esempio. Genera due valori distinti e casuali, ad esempio con Python:

```text
python -c "import secrets; print(secrets.token_urlsafe(24))"
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Usa il primo come `ADMIN_PASSWORD` e il secondo come `SESSION_SECRET`; copia i risultati nel file senza incollarli in chat o in un repository. Il nome admin iniziale è `organizzatore` e può essere cambiato prima del primo avvio. Il segnaposto `change-me` viene rifiutato all’inizializzazione.

In UGOS: apri **App Center → Docker → Project → Create**, scegli la cartella che contiene `docker-compose.yaml` e `.env`, quindi distribuisci il progetto. La lettura automatica di `.env` dall’interfaccia Project non è stata verificata; se l’interfaccia non carica l’ambiente, usa il terminale del NAS nella cartella del progetto:

```sh
docker compose --env-file .env -f docker-compose.yaml pull
docker compose --env-file .env -f docker-compose.yaml up -d
```

Compose scarica `ghcr.io/buiko32/mana-arena:latest`, pubblica la porta `3388` e conserva database e foto in due volumi Docker nominati (`mana-arena-db` e `mana-arena-photos`; Docker può anteporre il nome del progetto). Attendi lo stato `healthy`, poi apri `http://IP-DEL-NAS:3388` dalla rete locale. `/healthz` indica se l’app e il database sono pronti. Le viste pubbliche sono in sola lettura; l’accesso admin non ha registrazione pubblica.

Le password dei cookie sono HTTP compatibili per l’uso LAN (`COOKIE_SECURE=false`). Per l’accesso da Internet configura un reverse proxy HTTPS e imposta `COOKIE_SECURE=true`; gestisci certificato e firewall sul router separatamente. L’app non modifica impostazioni del router e non abilita HTTPS da sola.

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
node --test tests/brackets.test.cjs
```

GitHub Actions esegue gli stessi test prima di costruire l’immagine.

## Pubblicazione e limiti

GitHub Actions esegue i test, costruisce e prova l’immagine `linux/amd64`, poi pubblica su GHCR i tag `latest` e commit; i tag di release `v*` ricevono anche il tag di versione. Alla prima pubblicazione, rendi il package GHCR **Public** dalle impostazioni del package: la visibilità del repository non lo rende pubblico automaticamente. La modalità manuale `verify_public_only` prova il pull anonimo e l’avvio dell’immagine pubblica esistente senza login al registry.

La distribuzione sul NAS UGREEN non è stata eseguita né verificata. Il target del container è Linux amd64. Le regole di torneo sono amatoriali e non sostituiscono un software ufficiale di organizzazione o arbitraggio.
