"""Cruscotto Aziendale RCS - avvio del programma.

Genera un report HTML statico e autosufficiente leggendo in **sola lettura**
il database del gestionale RCS. Nessun server, nessuna dipendenza esterna.

Uso:
    python3 cruscotto.py              genera e apre il report nel browser
    python3 cruscotto.py --no-open    genera senza aprire il browser
    python3 cruscotto.py --cambia-db  sceglie un altro database e genera

La logica vera sta nel pacchetto ``nucleo/``: qui c'e' solo l'orchestrazione
e la gestione degli errori mostrati all'utente.
"""

import os
import sqlite3
import sys
import traceback
import webbrowser
from datetime import datetime

from config_db import (
    ConfigDbError,
    apri_db_sola_lettura,
    cambia_percorso_db,
    carica_impostazioni,
    risolvi_percorso_db,
)
from nucleo import estrazione, render, storico_prezzi
from nucleo.utils import LOG_PATH, log, percorso_dati


def _carica_notiziario():
    """Notizie di settore raccolte dal modulo opzionale ``notiziario.py``."""
    import json

    percorso = percorso_dati("notiziario_ultimo.json")
    if not os.path.isfile(percorso):
        return None
    try:
        with open(percorso, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log("notiziario_ultimo.json illeggibile: " + str(e))
        return None


def raccogli_dati(conn, oggi, imp):
    """Tutti i dati del report, gia' pronti per essere serializzati in JSON."""
    magazzino = estrazione.estrai_magazzino(conn, oggi, imp)

    # Unico punto in cui si scrive: registra i prezzi correnti nell'archivio
    # del Cruscotto e annota le righe con le variazioni osservate.
    archivio = storico_prezzi.registra(magazzino, oggi)
    storico_prezzi.annota(magazzino, archivio)

    return {
        "data_generazione": oggi.isoformat(),
        "impostazioni": {
            "giorni_allarme_esaurimento": imp.giorni_allarme_esaurimento,
            "giorni_attenzione_esaurimento": imp.giorni_attenzione_esaurimento,
            "soglia_aumento_consumo_pct": imp.soglia_aumento_consumo_pct,
            "soglia_aumento_prezzo_pct": imp.soglia_aumento_prezzo_pct,
            "soglia_risparmio_fornitore_pct": imp.soglia_risparmio_fornitore_pct,
            "soglia_riempimento_magazzino": imp.soglia_riempimento_magazzino,
            "max_consigli_per_tipo": imp.max_consigli_per_tipo,
        },
        "preventivi": estrazione.estrai_preventivi(conn),
        "magazzino": magazzino,
        "confronto_fornitori": estrazione.estrai_confronto_fornitori(conn),
        "movimenti": estrazione.estrai_movimenti(conn),
        "manodopera": estrazione.estrai_manodopera(conn),
        "materiali_piu_usati": estrazione.estrai_materiali_piu_usati(conn),
        "clienti": estrazione.estrai_clienti(conn),
        "ricarico": estrazione.estrai_ricarico(conn),
        "notiziario": _carica_notiziario(),
    }


def _pulisci_report_vecchi(cartella, da_mantenere):
    """Tiene solo gli ultimi report datati (``ultimo.html`` non si tocca).

    Ne viene creato uno a ogni esecuzione: senza pulizia la cartella
    crescerebbe all'infinito.
    """
    try:
        datati = sorted(
            f for f in os.listdir(cartella) if f.startswith("report_") and f.endswith(".html")
        )
    except OSError:
        return
    for nome in datati[:-da_mantenere] if len(datati) > da_mantenere else []:
        try:
            os.remove(os.path.join(cartella, nome))
        except OSError as e:
            log("Impossibile rimuovere il report vecchio " + nome + ": " + str(e))


def genera(percorso_db=None, apri_browser=True):
    """Genera il report e restituisce il percorso di ``ultimo.html``."""
    imp = carica_impostazioni()
    if percorso_db is None:
        percorso_db = risolvi_percorso_db()

    oggi = datetime.now()
    conn = apri_db_sola_lettura(percorso_db)
    try:
        dati = raccogli_dati(conn, oggi, imp)
    finally:
        conn.close()

    html = render.componi_html(dati, oggi)

    cartella_report = percorso_dati("report")
    os.makedirs(cartella_report, exist_ok=True)
    percorso_datato = os.path.join(cartella_report, "report_" + oggi.strftime("%Y%m%d_%H%M") + ".html")
    percorso_ultimo = os.path.join(cartella_report, "ultimo.html")
    for percorso in (percorso_datato, percorso_ultimo):
        with open(percorso, "w", encoding="utf-8") as f:
            f.write(html)

    _pulisci_report_vecchi(cartella_report, imp.report_da_mantenere)

    if apri_browser:
        webbrowser.open("file://" + os.path.abspath(percorso_ultimo))

    return percorso_ultimo


def main():
    apri_browser = "--no-open" not in sys.argv
    try:
        percorso_db = cambia_percorso_db() if "--cambia-db" in sys.argv else None
        if percorso_db:
            print("Nuovo database selezionato:", percorso_db)
        print("Report generato:", genera(percorso_db=percorso_db, apri_browser=apri_browser))
    except ConfigDbError as e:
        # Problema di configurazione: il messaggio e' gia' scritto per l'utente.
        print("Errore:", e)
        log("ConfigDbError: " + str(e))
        sys.exit(1)
    except sqlite3.Error as e:
        # Il database c'e' ma la lettura non riesce: tipicamente lo schema del
        # gestionale e' cambiato e manca una colonna attesa.
        print(
            "Errore nella lettura del database: " + str(e) +
            "\nControlla che il gestionale RCS sia aggiornato. Dettagli in " + LOG_PATH
        )
        log("sqlite3.Error: " + str(e), traceback.format_exc())
        sys.exit(1)
    except Exception as e:  # noqa: BLE001 - rete di sicurezza per l'utente finale
        # Nessun traceback grezzo in faccia a chi non e' tecnico: a video il
        # fatto, nel log i dettagli per la diagnosi.
        print(
            "Si e' verificato un errore imprevisto: il report non e' stato generato."
            "\nDettagli tecnici salvati in " + LOG_PATH
        )
        log("Errore imprevisto: " + str(e), traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
