# Cruscotto Aziendale RCS

Genera una pagina web con le statistiche dell'azienda leggendo i dati del
gestionale: giacenze di magazzino, quanto si consuma, ogni quanto si
rifornisce, cosa conviene ordinare e quando, andamento dei preventivi e dei
margini.

Il programma **non modifica mai** il database del gestionale: lo apre in sola
lettura. Il risultato è un unico file HTML che si apre come una normale pagina
web, anche senza internet.

---

## Come si usa

**Doppio clic su `Cruscotto.bat`** (oppure `python3 cruscotto.py`).

La prima volta chiede dove si trova il database del gestionale
(`materiali.db`): il percorso viene ricordato, le volte successive parte
diretto. Il report si apre da solo nel browser.

I file generati finiscono nella cartella `report/`: `ultimo.html` è sempre
l'ultimo generato, gli altri restano come archivio (vengono tenuti gli ultimi
30).

### Comandi

| Comando | Cosa fa |
|---|---|
| `python3 cruscotto.py` | genera il report e lo apre nel browser |
| `python3 cruscotto.py --no-open` | genera senza aprire il browser |
| `python3 cruscotto.py --cambia-db` | sceglie un altro database e genera |

---

## Cosa mostra il report

**In alto** sei numeri chiave del periodo scelto (mese, trimestre o anno), con
il confronto rispetto al periodo precedente. L'etichetta sotto il titolo dice
sempre quale periodo si sta guardando.

**In sintesi** — tre colonne in italiano semplice: cosa va bene, cosa merita
attenzione, cosa è cambiato.

**Magazzino** — la parte principale:

- giacenza attuale di ogni materiale, con scorta minima e punto di riordino;
- quanto se ne consuma al giorno e quanti giorni di autonomia restano;
- ogni quanti giorni si rifornisce di solito quel materiale;
- **consigli operativi**: quanto ordinare e entro quando, quali materiali
  stanno finendo, dove un altro fornitore costa meno, dove c'è troppo capitale
  fermo.

Cliccando su una riga della tabella si aprono i dettagli di quel materiale
(punto di riordino, tempo di consegna abituale, capitale fermo).

**Storico prezzi fornitore** — le variazioni di prezzo osservate. Attenzione:
il gestionale non conserva lo storico dei prezzi, quindi il Cruscotto comincia
a registrarli **da quando lo si usa la prima volta**. Gli aumenti già avvenuti
in passato non sono recuperabili.

**Il resto** — andamento dei preventivi, margini, composizione dei costi,
manodopera per fase, materiali più usati, clienti per valore, ricarico,
confronto fornitori, ultimi movimenti.

---

## Quando il programma dice "dati insufficienti"

Per stimare consumo e autonomia servono abbastanza movimenti registrati. Se un
materiale ne ha troppo pochi, il report lo dice apertamente invece di mostrare
un numero inventato. Più si registrano carichi e scarichi nel gestionale, più
le stime diventano affidabili.

---

## Regolare le soglie

Le soglie (quando un materiale è "in allarme", quale aumento di prezzo
segnalare, ecc.) si cambiano in `config.json`, senza toccare il programma.
Vedi `config.example.json` per l'elenco completo con le spiegazioni.

Esempio — considerare "in allarme" un materiale con meno di 21 giorni di
autonomia invece di 14:

```json
{
  "db_path": "C:/percorso/al/database/materiali.db",
  "impostazioni": { "giorni_allarme_esaurimento": 21 }
}
```

---

## Se qualcosa non funziona

Il programma scrive un file `cruscotto.log` accanto a sé con i dettagli
tecnici di ogni errore. È la prima cosa da guardare, soprattutto se lanciato
con doppio clic (la finestra nera si chiude troppo in fretta per leggere il
messaggio).

Problemi frequenti:

- **"Il file selezionato non sembra il database del gestionale RCS"** — è
  stato scelto un file `.db` sbagliato. Rilancia con `--cambia-db`.
- **I grafici non compaiono** — serve internet la prima volta, per scaricare
  la libreria dei grafici e metterla in cache. Numeri e tabelle restano
  comunque leggibili.
- **"Errore nella lettura del database"** — di solito lo schema del gestionale
  è cambiato. Il dettaglio è nel log.

---

## Per chi sviluppa

### Struttura

```
cruscotto.py          avvio: legge, genera, gestisce gli errori
config_db.py          dove sta il database e con quali soglie lavorare
nucleo/
  utils.py            numeri, date, percorsi, log
  impostazioni.py     soglie, sovrascrivibili da config.json
  analisi.py          matematica del magazzino (pura, senza I/O)
  estrazione.py       query sul gestionale (sola lettura)
  storico_prezzi.py   archivio prezzi del Cruscotto
  render.py           assemblaggio del file HTML
assets_web/
  stile.css           foglio di stile del report
  app.js              logica del report
test/                 test automatici
```

Il report finale resta **un unico file HTML**: CSS e JavaScript vengono
incorporati al momento della generazione, ma si scrivono come file veri, con
evidenziazione della sintassi e test.

### Test

```bash
python3 esegui_test.py
```

Non serve installare niente: i test Python usano `unittest`, quelli JavaScript
il runner incluso in Node (se Node manca, quella parte viene saltata).

Coprono soprattutto i punti dove è facile sbagliare senza accorgersene: le
finestre temporali dei consumi, il calcolo del margine, il punto di riordino,
e i file di appoggio rovinati.

### Regole da rispettare

- **Sola lettura sul gestionale**, sempre. Ogni connessione passa da
  `config_db.apri_db_sola_lettura()`.
- **Solo libreria standard Python**: deve restare compilabile in un `.exe`
  singolo senza sorprese.
- La **giacenza vera** è la somma per fornitore in `materiale_fornitori`: la
  colonna `materiali.giacenza` nel database del gestionale resta sempre a zero
  e non va usata.
- Quando un dato non è calcolabile si restituisce `None` e il report lo dice:
  mai uno zero o una stima inventata al posto di un dato mancante.
