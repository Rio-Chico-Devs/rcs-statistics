"""Risoluzione e apertura in sola lettura del database del gestionale RCS.

Il Cruscotto e' un progetto indipendente dal gestionale: non conosce la
cartella del gestionale, non sa dove sono i suoi file. L'unico modo in cui
trova il database e' tramite ``config.json`` (percorso assoluto salvato qui
dentro), con un selettore grafico come ripiego al primo avvio.
"""

import json
import os
import sqlite3
import urllib.request

TABELLE_ATTESE = (
    "materiali",
    "categorie_materiale",
    "fornitori",
    "materiale_fornitori",
    "movimenti_magazzino",
    "preventivi",
    "clienti",
)

CARTELLA_PROGETTO = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(CARTELLA_PROGETTO, "config.json")


class ConfigDbError(Exception):
    """Il database non e' stato trovato o non e' quello atteso."""


def apri_db_sola_lettura(percorso):
    """Apre ``percorso`` in sola lettura tramite URI sqlite (``mode=ro``).

    Non usare mai ``sqlite3.connect(percorso)`` direttamente altrove nel
    progetto: solo questa funzione garantisce che non si possa scrivere.
    """
    percorso_assoluto = os.path.abspath(percorso)
    uri = "file:" + urllib.request.pathname2url(percorso_assoluto) + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _percorso_da_config():
    if not os.path.isfile(CONFIG_PATH):
        return None
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            dati = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    percorso = dati.get("db_path")
    if percorso and os.path.isfile(percorso):
        return percorso
    return None


def _percorso_da_selettore():
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        return None
    root = tk.Tk()
    root.withdraw()
    percorso = filedialog.askopenfilename(
        title="Seleziona il database del gestionale RCS (materiali.db)",
        filetypes=[("Database SQLite", "*.db"), ("Tutti i file", "*.*")],
    )
    root.destroy()
    return percorso or None


def _salva_config(percorso):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({"db_path": percorso}, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


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


def risolvi_percorso_db():
    """Trova il database: prima ``config.json``, poi il selettore grafico.

    Valida che le tabelle attese esistano prima di restituire il percorso;
    solleva :class:`ConfigDbError` se non trova nulla di valido.
    """
    percorso = _percorso_da_config()
    if percorso is None:
        percorso = _percorso_da_selettore()
        if percorso is None:
            raise ConfigDbError(
                "Nessun database configurato. Crea 'config.json' nella "
                "cartella del programma (vedi config.example.json) con il "
                "percorso del file .db, oppure riavvia e selezionalo dalla "
                "finestra che si apre."
            )
        _salva_config(percorso)
    _valida_tabelle(percorso)
    return percorso
