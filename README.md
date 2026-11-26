# Mana Arena

Mana Arena è un’app self-hosted in italiano per organizzare tornei amatoriali di Magic, registrare partite e tenere i punti vita al tavolo.

## Funzionalità

- **Tornei 1v1** fino a 64 partecipanti, con abbinamenti, turni, tabelloni, classifiche ed esportazione CSV.
- **Amichevoli** in duello, a squadre e tutti contro tutti. Lo storico mostra risultati e variazioni Elo.
- **Profili e classifica globale** con andamento Elo, risultati recenti, frammenti di mana e missioni personali che ruotano ogni lunedì, fino a 60 frammenti extra a settimana.
- **Segnavita per telefono** con vita iniziale personalizzabile, dado e salvataggio del risultato. Funziona anche offline dopo il primo caricamento in HTTPS e sincronizza quando torna la connessione.

La classifica Elo è interna. Le regole dei tornei sono pensate per il gioco amatoriale.

## Avvio e aggiornamento

Serve Docker Compose su Linux amd64, anche su NAS UGREEN. Nella cartella del progetto, copia [`.env.example`](.env.example) in `.env` e imposta tre valori distinti per `SHARED_ACCESS_PASSWORD` (almeno 12 caratteri), `ADMIN_PASSWORD` e `SESSION_SECRET`.

Usa gli stessi comandi per installare o aggiornare l’app.

```sh
docker compose pull
docker compose up -d
```

[Compose](docker-compose.yaml) usa l’immagine `ghcr.io/buiko32/mana-arena:latest` e pubblica la porta **3388**. Lo stato dell’app è disponibile su `/healthz`.

Condividi con i giocatori solo la password condivisa. Per l’accesso da Internet usa un reverse proxy HTTPS con `COOKIE_SECURE=true`. Per una prova locale in HTTP imposta `COOKIE_SECURE=false`.

## Dati e backup

Da **Organizza → Backup** puoi scaricare e ripristinare il database dei tornei e le foto dei profili. L’archivio del Segnavita è separato. Per salvarlo, arresta il servizio e copia la cartella indicata da `LIFE_STATS_HOST_PATH` (predefinita `./life-stats`).

## Sviluppo

Con Python 3.12 e Node.js installati, esegui i test dalla radice del progetto.

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
node --test tests/*.test.cjs tests/test_life*.cjs
```

[GitHub Actions](https://github.com/Buiko32/mana-arena/actions) esegue i test e pubblica l’immagine Docker. Per modifiche all’app, aggiorna la versione nel [footer](app/static/index.html).
