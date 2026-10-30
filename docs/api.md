# Mana Arena API

L'API è same-origin. JSON usa UTF-8 e i messaggi di validazione sono in italiano. Gli ID sono stringhe UUID. Gli endpoint pubblici sono solo lettura.

## Ambiente e avvio

GET /healthz verifica SQLite con PRAGMA quick_check e risponde 200 {"status":"ok"}; un errore del database produce 503. GET / serve app/static/index.html quando presente. /static/* serve asset locali. /photos/{uuid}.jpg accetta solo nomi immagine generati dal server.

| Variabile | Comportamento |
| --- | --- |
| DATA_DIR | Directory persistente, predefinita /data; DB in db/mana-arena.sqlite3 e immagini in photos/. |
| ADMIN_USERNAME | Nome iniziale, predefinito admin; con un DB esistente deve coincidere con l'admin registrato. |
| ADMIN_PASSWORD | Richiesta solo alla prima inizializzazione; almeno 12 caratteri, segnaposto rifiutati. Non viene reimpostata ai riavvii. |
| SESSION_SECRET | Obbligatoria, almeno 32 byte; usata per la firma HMAC-SHA256 delle sessioni. |
| COOKIE_SECURE | false per HTTP LAN; impostare true dietro HTTPS. Default false. |

Il server non si fida di X-Forwarded-* e non offre registrazione pubblica.

## Sessione e CSRF

POST /api/admin/login accetta {"username":"...","password":"..."}. Successo: 200 {"username":"...","csrf_token":"...","expires_in":28800}; imposta mana_session (HttpOnly, SameSite=Lax, scadenza 8 ore) e mana_csrf (SameSite=Lax). I tentativi sono limitati per IP di connessione e nome account: credenziali errate 401, blocco temporaneo 429. L'errore non rivela quale credenziale fosse errata. La sessione firmata è registrata nel DB e può essere revocata.

GET /api/admin/session restituisce username, csrf_token e expires_at. Ogni richiesta mutante richiede i cookie e X-CSRF-Token con il valore csrf_token. Sessione assente/scaduta: 401; CSRF assente o discordante: 403. Il login è l'unica mutazione senza sessione/CSRF.

POST /api/admin/logout non ha body; richiede CSRF, revoca la sessione e rimuove i cookie. POST /api/admin/password accetta {"current_password":"...","new_password":"..."}; la nuova password deve avere almeno 12 caratteri e non essere un segnaposto. Successo {"ok":true}; tutte le sessioni sono revocate. Le password usano PBKDF2-HMAC-SHA256, salt casuale e 600.000 iterazioni.

## Configurazione

Create/update invia {"config": {...}}. Esempio:

    {"name":"Serata Mana","date":"2026-10-30","description":"","structure":"swiss","game_format":"Modern","match_length":1,"rounds":3,"loss_threshold":null,"league_mode":null,"seeding":"manual","allow_draws":false,"points":{"win":3,"draw":1,"loss":0,"bye":3},"tiebreakers":["omw","wins","game_diff"]}

structure: swiss, adapted, league, single_elim. match_length: 1 (BO1) o 3 (BO3). Swiss rounds 1–32; adapted loss_threshold 2–5; league_mode single/double; seeding manual/random. Punti interi 0–10, vittoria maggiore della sconfitta; patte e punteggio draw devono essere compatibili con la struttura. tiebreakers è un ordine senza duplicati fra omw, wins, game_diff; seed è sempre l'ultimo criterio. All'avvio la configurazione è congelata.

## Lettura pubblica

- GET /api/public/tournaments: tornei active e completed; ogni elemento ha id, config, status, champion_id.
- GET /api/public/tournaments/{id}: id, configurazione effettiva, status, entrants, matches, rounds, standings, champion_id, warnings. L'iscritto pubblico contiene solo id, name, photo_url, seed e withdrawn.
- GET /api/public/tournaments/{id}/standings: righe player_id, seed, points, wins, draws, losses, byes, game_diff, omw, rank, eliminated.
- GET /api/public/tournaments/{id}/rounds: array di {round, matches}; conserva i campi prodotti dal motore, compresi bracket_slot e i risultati bye/void.

Bozze e tornei archiviati restituiscono 404. Le viste pubbliche non includono audit, hash password, sessioni o dati amministrativi.

## Giocatori e foto

| Metodo e rotta | Body / risposta |
| --- | --- |
| GET /api/admin/players | Array dei profili attivi: id, name, photo_url, active. |
| POST /api/admin/players | JSON {"name":"..."}; crea e risponde 201 con profilo. |
| PATCH /api/admin/players/{player_id} | JSON {"name":"..."}; restituisce profilo aggiornato. |
| DELETE /api/admin/players/{player_id} | Soft-delete; 409 se il profilo ha riferimenti storici. |
| POST /api/admin/players/{player_id}/photo | Multipart file; max 5 MiB, JPEG/PNG/WebP decodificabili, pixel limitati. Risponde {"photo_url":"/photos/<uuid>.jpg"}. |

La foto è rielaborata in JPEG per eliminare metadati. Tipo/dimensioni non supportati: 422; oltre limite: 413. SVG, URL, percorsi e nomi client non sono accettati.

## Tornei e flussi admin

| Metodo e rotta | Body e comportamento |
| --- | --- |
| GET /api/admin/tournaments | Elenco di tutti gli stati. |
| POST /api/admin/tournaments | {"config":{...}}; crea bozza, 201. |
| GET /api/admin/tournaments/{id} | Oggetto con id, config, frozen_config, status, revision, warnings, champion_id, archived_from, entrants, matches, rounds e standings. Ogni entrant conserva i campi amministrativi (id, seed, withdrawn) e include name e photo_url. Rounds è raggruppato come {round,matches}; standings usa le righe del motore. Disponibile in draft, active, completed e archived; non richiede che il torneo sia pubblico. |
| PUT /api/admin/tournaments/{id}/config | {"config":{...}}; modifica una bozza. In active restituisce 409 con dettaglio affected_rounds/affected_results e requires_confirmation. Confermare con ?confirm_rollback=true: elimina risultati e turni, resetta ritiri, mantiene iscritti e audit e torna a bozza. Completed e archived richiedono prima POST reopen esplicito, anche se viene presentato confirm_rollback=true; il rollback resta una seconda conferma separata. |
| DELETE /api/admin/tournaments/{id} | Elimina solo una bozza. |
| POST /api/admin/tournaments/{id}/entrants | {"player_ids":["uuid",...]}; iscrive profili attivi univoci, massimo 64; restituisce array iscritti ordinati per seed. |
| PUT /api/admin/tournaments/{id}/entrants/{player_id} | {"seed":1}; aggiorna il seed in bozza. |
| DELETE /api/admin/tournaments/{id}/entrants/{player_id} | Rimuove iscrizione solo in bozza. |
| POST /api/admin/tournaments/{id}/start | {}; richiede 2–64 iscritti, congela config. Crea il calendario league o il primo bracket single-elimination. Il seed casuale viene conservato nella config effettiva e nell'audit. |
| POST /api/admin/tournaments/{id}/rounds/next | {}; crea Swiss/adapted/elimination round dopo il completamento precedente. Restituisce {round,matches,warnings,revision}. Calendario league creato all'avvio. Ripetizioni/conflitti restituiscono 409. |
| PUT /api/admin/tournaments/{id}/matches/{match_id}/result | {"result":"p1_win","p1_games":1,"p2_games":0}. Esiti p1_win, p2_win, draw, p1_forfeit, p2_forfeit. Il bye è finalizzato dal server. BO1/BO3 e compatibilità patta validati; duplicato 409, input invalido 422. Risposta contiene match finalizzato e status. |
| POST /api/admin/tournaments/{id}/withdrawals | {"player_id":"uuid"}; ritira, assegna forfait agli incontri aperti consentiti, registra audit. La risposta include effective_round: Swiss/adapted/elimination usa il turno corrente se aperto, altrimenti l'ultimo turno più uno; league usa il primo turno schedulato aperto di quel partecipante. Già ritirato: 409. |
| POST /api/admin/tournaments/{id}/rollback | {"match_id":"uuid"}; preview risponde 409 con detail.requires_confirmation, affected_rounds, affected_results, match_ids. Ripetere con confirm:true per invalidare il turno selezionato e quelli dipendenti; registrare poi il risultato nel turno rigenerato. Nel league il calendario completo resta presente e i risultati successivi sono azzerati, mantenendo quelli precedenti. Ritiri con effective_round raggiunto dal rollback vengono rimossi dai dati operativi; ritiri precedenti restano applicati. L'audit conserva la cronologia. |
| POST /api/admin/tournaments/{id}/reset | {"confirm":true}; elimina incontri e ritiri, conserva iscritti/audit e torna a bozza. |
| POST /api/admin/tournaments/{id}/archive | Archivia senza eliminare e memorizza lo stato precedente. |
| POST /api/admin/tournaments/{id}/reopen | Da archived ripristina esattamente lo stato memorizzato. Da completed passa ad active per la riapertura esplicita; per cambiare config serve poi la conferma full rollback. Se un torneo completed era archiviato, servono prima reopen per tornare a completed e poi un secondo reopen per passare ad active. |
| GET /api/admin/tournaments/{id}/audit | Array eventi con id, action, detail, created_at. |

Errori business in forma {"detail":{"message":"...", ...}}. Codici: 401 sessione; 403 CSRF; 404 ID non trovato/torneo non pubblico; 409 conflitto, stato o conferma richiesta; 413 file troppo grande; 422 input/regole non validi; 429 login limitato. Il client mostra detail.message e usa gli altri campi detail come dati strutturati.

## CSV e backup

- GET /api/admin/tournaments/{id}/export.csv?kind=standings scarica CSV UTF-8 BOM. kind: standings, entrants o matches; filename controllato. I campi che iniziano con =, +, -, @, tab o CR sono prefissati con apostrofo.
- GET /api/admin/backup scarica mana-arena-backup.zip e richiede sessione. Usa sqlite3.Connection.backup per la snapshot e include manifest, DB autonomo e foto server-generated.
- POST /api/admin/restore?confirm=true usa multipart file, massimo 100 MiB. Conferma richiesta. Verifica versione DB, tutte le tabelle e colonne richieste, admin/schema, foreign key, hash, path, duplicati, symlink, rapporto di compressione, dimensione e integrità SQLite; inizializza e revoca sessioni nello staging prima della sostituzione. DB, foto e sessioni precedenti restano disponibili fino al completamento di ogni verifica post-installazione; errori ripristinano il vecchio stato. Risposta {"ok":true,"restored_photos":n}; tutte le sessioni vengono revocate dopo il successo.

Manifest v1: version 1, database db/mana-arena.sqlite3, files [{path,size,sha256}].
