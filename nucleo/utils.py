"""Funzioni di servizio condivise: numeri, date, percorsi, log."""

import os
import sys
from datetime import datetime

# Cartella dove stanno i sorgenti/le risorse di sola lettura (CSS, JS).
# Con PyInstaller in modalita' onefile i file vengono estratti in una
# cartella temporanea indicata da sys._MEIPASS.
CARTELLA_SORGENTI = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Cartella dove il programma SCRIVE (config, storico prezzi, log, report).
# Quando gira come .exe non deve scrivere dentro _MEIPASS (temporanea, viene
# cancellata all'uscita): scrive accanto all'eseguibile, dove l'utente la trova.
if getattr(sys, "frozen", False):
    CARTELLA_DATI = os.path.dirname(os.path.abspath(sys.executable))
else:
    CARTELLA_DATI = CARTELLA_SORGENTI

LOG_PATH = os.path.join(CARTELLA_DATI, "cruscotto.log")


def percorso_risorsa(*parti):
    """Percorso di una risorsa di sola lettura inclusa nel programma."""
    base = getattr(sys, "_MEIPASS", CARTELLA_SORGENTI)
    return os.path.join(base, *parti)


def percorso_dati(*parti):
    """Percorso di un file che il programma scrive (accanto all'eseguibile)."""
    return os.path.join(CARTELLA_DATI, *parti)


def log(messaggio, dettaglio=""):
    """Appende una riga al log del Cruscotto.

    Quando il programma gira come .exe lanciato con doppio clic la console si
    chiude subito e i messaggi a video svaniscono: il file di log e' l'unico
    modo per capire cosa e' andato storto dopo il fatto. Non deve mai far
    fallire il programma, quindi ogni errore di I/O sul log viene ignorato.
    """
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(datetime.now().isoformat(timespec="seconds") + "  " + messaggio + "\n")
            if dettaglio:
                f.write(dettaglio.rstrip() + "\n")
    except OSError:
        pass


def num(valore):
    """Converte in float qualsiasi cosa, con 0.0 come ripiego.

    I campi numerici del gestionale possono essere NULL o stringhe vuote:
    nessun calcolo a valle deve dover gestire ``None``.
    """
    if valore is None:
        return 0.0
    try:
        return float(valore)
    except (TypeError, ValueError):
        return 0.0


def parse_data(valore):
    """Converte una data ISO del gestionale in ``datetime``, o ``None``.

    Difensiva di proposito: una singola riga con data sporca non deve far
    fallire l'intero report. Chi chiama decide se saltare la riga.
    """
    if not valore:
        return None
    if isinstance(valore, datetime):
        return valore
    try:
        return datetime.fromisoformat(str(valore))
    except (TypeError, ValueError):
        return None


def mediana(valori):
    """Mediana di una lista di numeri (``None`` se vuota).

    Preferita alla media dove un singolo valore anomalo falserebbe il
    risultato (es. un riordino d'emergenza fra due ordini regolari).
    """
    ordinati = sorted(valori)
    n = len(ordinati)
    if n == 0:
        return None
    meta = n // 2
    if n % 2 == 1:
        return float(ordinati[meta])
    return (ordinati[meta - 1] + ordinati[meta]) / 2.0
