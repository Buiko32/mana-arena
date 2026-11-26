# Mana Arena — Piano delle prossime modifiche

Base di lavoro **0.01.42**, pubblicata su GitHub il 26 novembre 2026. Il piano raccoglie le funzionalità già disponibili, le regole concordate per i prossimi sviluppi e le decisioni ancora aperte. [Codice della 0.01.42](https://github.com/Buiko32/mana-arena/commit/1626f65e225c6ae6417c220018ce882f3c4bdc59) · [Test e pubblicazione Docker](https://github.com/Buiko32/mana-arena/actions/runs/45776436488)

## Funzionalità già pubblicate

| Area | Stato nella 0.01.42 |
|---|---|
| Classifica | È evidenziata solo la riga del profilo selezionato. Ogni giocatore ha il proprio saldo Frammenti Mana con l’icona del cristallo. |
| Profili | Il piazzamento Elo mostra il conteggio e la barra fino a 30/30. Le etichette dei tutti contro tutti entrano nei riquadri delle ultime cinque partite. Il profilo mostra il saldo e indica il vincolo di utilizzo per chi occupa le prime tre posizioni. |
| Live | Schede compatte con avatar e PV, animazioni per i guadagni e le perdite di vita, ultimi valori visibili durante un’interruzione degli aggiornamenti e accesso dal torneo. |
| Storico | Calendario compatto con filtri per avversario, formato e proprie partite. |
| Segnavita | Pulsante Termina partita in rosso e D20 orientato verso ciascun giocatore. Nel tutti contro tutti si aggiungono da 3 a 5 giocatori distinti e ognuno riceve automaticamente un contatore. Alla fine sono proposti piazzamenti modificabili con le foto profilo, usando i PV e l’ordine di discesa sotto zero. |
| Riepilogo confermato | Per ciascun partecipante mostra variazione Elo, posizione in classifica e Frammenti ottenuti. Il tutti contro tutti supporta tavoli da 3, 4 e 5 giocatori. |

### Regole di guadagno già attive

| Evento | Frammenti |
|---|---:|
| Partecipazione a una partita confermata, compresi sconfitta e pareggio | +1 |
| Vittoria confermata | +2 aggiuntivi |
| Terza vittoria consecutiva in amichevole | +10 aggiuntivi |
| Quinta vittoria consecutiva in amichevole | +20 aggiuntivi |
| Vittoria di un torneo concluso | +30 aggiuntivi |

I premi della terza e della quinta vittoria si cumulano. Una nuova serie di amichevoli può farli ottenere nuovamente. Il saldo attuale viene ricavato dallo storico confermato. La 0.01.42 offre il guadagno e la visualizzazione del saldo. Acquisti e attivazione dei bonus sono sviluppi successivi.

### Regola tutti contro tutti dalla 0.01.44

L’ultimo riceve 1 Frammento, ogni posizione superiore ne vale uno in più e il vincitore riceve anche il bonus di vittoria da 2. Dal primo all’ultimo, i premi sono `5, 2, 1` in 3, `6, 3, 2, 1` in 4 e `7, 4, 3, 2, 1` in 5. Il premio di partecipazione è compreso. Le serie si aggiungono al premio del tavolo.

### Riepilogo con podio dalla 0.01.44

- Con 3 giocatori risalta solo il vincitore. Con 4 e 5 giocatori il podio mostra le foto dei primi tre, con il vincitore al centro e il secondo in argento su un gradino più alto del terzo. Gli altri piazzamenti appaiono in righe compatte.
- Il riepilogo mostra il movimento Elo e i Frammenti accanto al piazzamento. Il movimento nella classifica globale si apre dal dettaglio dedicato.
- Nel segnavita il riepilogo usa i piazzamenti del risultato dopo la sincronizzazione e la conferma.

## Prossime modifiche

### 1. Traguardi e catalogo delle ricompense

- Nel profilo mostrare saldo, progresso verso il prossimo traguardo e ricompense disponibili.
- Aggiungere un catalogo di personalizzazioni acquistabili con Frammenti Mana. Il primo catalogo proposto include cornici per avatar, temi del segnavita e animazioni di vittoria.
- Registrare gli acquisti e le ricompense del giocatore in modo che siano disponibili anche da un altro dispositivo. Definire come le correzioni dei risultati influiscono sui Frammenti già spesi.

### 2. Potenziamenti per le amichevoli

| Potenziamento | Effetto |
|---|---|
| Boost +5 | Aggiunge 5 Elo a una vittoria o riduce di 5 una perdita, fino a zero. |
| Boost +10 | Aggiunge 10 Elo a una vittoria o riduce di 10 una perdita, fino a zero. |
| Doppio D20 | Tira due dadi per decidere chi inizia e usa il valore più alto. |
| Ritira D20 | Consente un rilancio dopo aver visto il primo tiro. Il secondo valore sostituisce il primo. |

- I potenziamenti si usano nelle amichevoli e si attivano prima dell’inizio. Il tavolo vede quali bonus sono attivi.
- Il bonus resta riservato alla partita fino alla conferma. Una partita annullata libera il bonus. Un reinvio dello stesso risultato deve produrre un solo consumo.
- I primi tre in classifica continuano a guadagnare Frammenti, ma il loro utilizzo è sospeso. Applicare il vincolo anche sul server quando saranno disponibili le operazioni di spesa.
- Nel riepilogo indicare il contributo del Boost separatamente dal normale movimento Elo, per esempio `−8 Elo → −3 Elo con Boost +5`.

## Decisioni da chiudere prima degli acquisti

| Decisione | Da definire |
|---|---|
| Prezzi e catalogo | Costo dei quattro potenziamenti e delle prime personalizzazioni. |
| Cumulo dei bonus | La proposta è un bonus per giocatore a partita. Decidere se Elo e D20 possano essere abbinati. |
| Cambio di posizione | Stabilire se un bonus già riservato rimane valido quando il giocatore entra nei primi tre prima della conferma. |
| Identità e acquisti | Oggi il profilo si seleziona per nome. Definire come assicurare che solo il giocatore autorizzato possa spendere il saldo e attivare i bonus. |
| Boost nei tutti contro tutti | Definire l’effetto su ogni piazzamento, in particolare quando un giocatore fuori dal primo posto guadagna comunque Elo. |

L’ordine proposto è introdurre catalogo e personalizzazioni, poi attivare i potenziamenti dopo aver definito le regole di spesa e conferma.
