"""Configurazione: dove sta il database e con quali soglie lavorare.

Il Cruscotto e' un progetto indipendente dal gestionale: non conosce la sua
cartella, non sa dove sono i suoi file. L'unico modo in cui trova il database
e' ``config.json`` (percorso assoluto salvato qui dentro), con un selettore
grafico come ripiego al primo avvio.

Il database viene sempre aperto in **sola lettura**: nessuna funzione di
questo progetto puo' modificare i dati del gestionale.
"""

import json
import os
import sqlite3
import urllib.request

from nucleo.impostazioni import Impostazioni
from nucleo.utils import log, percorso_dati

TABELLE_ATTESE = (
    "materiali",
    "categorie_materiale",
    "fornitori",
    "materiale_fornitori",
    "movimenti_magazzino",
    "preventivi",
    "clienti",
)

CONFIG_PATH = percorso_dati("config.json")


class ConfigDbError(Exception):
    """Il database non e' stato trovato o non e' quello atteso."""


def apri_db_sola_lettura(percorso):
    """Apre ``percorso`` in sola lettura tramite URI sqlite (``mode=ro``).

    Non usare mai ``sqlite3.connect(percorso)`` direttamente altrove: solo
    questa funzione garantisce che non si possa scrivere sul gestionale.
    """
    uri = "file:" + urllib.request.pathname2url(os.path.abspath(percorso)) + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _leggi_config():
    if not os.path.isfile(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            dati = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log("config.json illeggibile: " + str(e))
        return {}
    return dati if isinstance(dati, dict) else {}


def carica_impostazioni():
    """Impostazioni di analisi: i default, sovrascritti da ``config.json``."""
    return Impostazioni.da_dizionario(_leggi_config().get("impostazioni"))


def _percorso_da_config():
    percorso = _leggi_config().get("db_path")
    if percorso and os.path.isfile(percorso):
        return percorso
    return None


def _percorso_da_selettore():
    """Finestra di selezione file. ``None`` se annullata o senza interfaccia."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        log("tkinter non disponibile: impossibile aprire il selettore file")
        return None
    root = tk.Tk()
    root.withdraw()
    try:
        percorso = filedialog.askopenfilename(
            title="Seleziona il database del gestionale RCS (materiali.db)",
            filetypes=[("Database SQLite", "*.db"), ("Tutti i file", "*.*")],
        )
    finally:
        root.destroy()
    return percorso or None


def _salva_percorso(percorso):
    """Aggiorna solo ``db_path``, conservando le altre chiavi (impostazioni)."""
    dati = _leggi_config()
    dati["db_path"] = percorso
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(dati, f, ensure_ascii=False, indent=2)
    except OSError as e:
        log("Impossibile salvare config.json: " + str(e))


def _valida_tabelle(percorso):
    conn = apri_db_sola_lettura(percorso)
    try:
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        presenti = {r["name"] for r in cur.fetchall()}
    finally:
        conn.close()
    mancanti = [t for t in TABELLE_ATTESE if t not in presenti]
    if mancanti:
        raise ConfigDbError(
            "Il file selezionato non sembra il database del gestionale RCS "
            "(mancano le tabelle: " + ", ".join(mancanti) + ")."
        )


def cambia_percorso_db():
    """Forza la scelta di un nuovo database e aggiorna ``config.json``.

    A differenza di :func:`risolvi_percorso_db` non guarda la configurazione
    esistente: serve per cambiare database in qualsiasi momento, non solo al
    primo avvio.
    """
    percorso = _percorso_da_selettore()
    if percorso is None:
        raise ConfigDbError(
            "Nessun file selezionato: resta configurato il database precedente."
        )
    _valida_tabelle(percorso)
    _salva_percorso(percorso)
    return percorso


def risolvi_percorso_db():
    """Trova il database: prima ``config.json``, poi il selettore grafico."""
    percorso = _percorso_da_config()
    if percorso is None:
        percorso = _percorso_da_selettore()
        if percorso is None:
            raise ConfigDbError(
                "Nessun database configurato. Crea 'config.json' nella cartella "
                "del programma (vedi config.example.json) indicando il percorso "
                "del file .db, oppure riavvia e selezionalo dalla finestra che "
                "si apre."
            )
        _salva_percorso(percorso)
    _valida_tabelle(percorso)
    return percorso
