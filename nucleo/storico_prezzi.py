"""Archivio dei prezzi fornitore, tenuto dal Cruscotto.

Il database del gestionale conserva solo il prezzo *attuale* di ogni
materiale: nessuno storico, quindi gli aumenti gia' avvenuti non sono
recuperabili in nessun modo. Questo modulo tiene un archivio a parte (file
del Cruscotto, mai il database del gestionale, che resta in sola lettura)
per poter mostrare le variazioni **da qui in avanti**.

Si registra una rilevazione solo quando il prezzo cambia davvero, non a ogni
generazione del report: altrimenti il file si riempirebbe di doppioni.
"""

import json
import os

from .utils import log, num, percorso_dati

NOME_FILE = "storico_prezzi.json"


def percorso_archivio():
    return percorso_dati(NOME_FILE)


def carica():
    """Legge l'archivio. Un file mancante, corrotto o di formato sbagliato non
    e' un errore fatale: si riparte da vuoto annotandolo nel log.
    """
    percorso = percorso_archivio()
    if not os.path.isfile(percorso):
        return {}
    try:
        with open(percorso, "r", encoding="utf-8") as f:
            dati = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log(NOME_FILE + " illeggibile, riparto da vuoto: " + str(e))
        return {}
    if not isinstance(dati, dict):
        log(NOME_FILE + " non e' un oggetto JSON, ignorato")
        return {}
    return dati


def salva(storico):
    """Scrive l'archivio in modo atomico.

    Prima su un file temporaneo, poi rinomina: se il programma viene chiuso a
    meta' scrittura l'archivio precedente resta intatto invece di troncarsi.
    """
    percorso = percorso_archivio()
    temporaneo = percorso + ".tmp"
    try:
        with open(temporaneo, "w", encoding="utf-8") as f:
            json.dump(storico, f, ensure_ascii=False, indent=2)
        os.replace(temporaneo, percorso)
    except OSError as e:
        log("Impossibile salvare " + NOME_FILE + ": " + str(e))
        try:
            os.remove(temporaneo)
        except OSError:
            pass


def registra(righe_magazzino, oggi, storico=None):
    """Aggiunge una rilevazione per ogni materiale il cui prezzo e' cambiato.

    Restituisce l'archivio aggiornato (in memoria) e lo salva su disco solo
    se qualcosa e' effettivamente cambiato.
    """
    storico = carica() if storico is None else storico
    cambiato = False
    for riga in righe_magazzino:
        prezzo_attuale = riga.get("prezzo_fornitore_medio", 0.0)
        if prezzo_attuale <= 0:
            continue
        chiave = str(riga["materiale_id"])
        voci = storico.get(chiave)
        if not isinstance(voci, list):
            voci = storico[chiave] = []
        ultimo = voci[-1].get("prezzo_fornitore") if voci and isinstance(voci[-1], dict) else None
        if ultimo is None or round(num(ultimo), 2) != round(prezzo_attuale, 2):
            voci.append({"data": oggi.isoformat(), "prezzo_fornitore": round(prezzo_attuale, 2)})
            cambiato = True
    if cambiato:
        salva(storico)
    return storico


def _riepilogo(voci):
    """Campi derivati dalle rilevazioni di un singolo materiale."""
    voci = [v for v in voci if isinstance(v, dict) and "prezzo_fornitore" in v]
    if not voci:
        return {
            "n_rilevazioni_prezzo": 0,
            "primo_prezzo_rilevato": None,
            "prima_rilevazione_prezzo": None,
            "variazione_prezzo_pct": None,
        }
    primo = num(voci[0]["prezzo_fornitore"])
    ultimo = num(voci[-1]["prezzo_fornitore"])
    variazione = None
    if len(voci) >= 2 and primo > 0:
        variazione = round((ultimo - primo) / primo * 100, 1)
    return {
        "n_rilevazioni_prezzo": len(voci),
        "primo_prezzo_rilevato": round(primo, 2),
        "prima_rilevazione_prezzo": voci[0].get("data"),
        "variazione_prezzo_pct": variazione,
    }


def annota(righe_magazzino, storico):
    """Aggiunge a ogni riga di magazzino i campi derivati dallo storico.

    Sola lettura del dizionario gia' caricato: nessun I/O.
    """
    for riga in righe_magazzino:
        voci = storico.get(str(riga["materiale_id"]), [])
        riga.update(_riepilogo(voci if isinstance(voci, list) else []))
    return righe_magazzino
