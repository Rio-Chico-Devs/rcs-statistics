"""Matematica del magazzino: consumo, autonomia, riordino.

Modulo **puro**: nessuna lettura di database, nessuna scrittura su disco,
nessuna dipendenza dal formato HTML. Prende movimenti gia' letti e restituisce
numeri. E' cosi' che si riesce a testarlo davvero (vedi ``test/test_analisi.py``).

Convenzioni:
- i movimenti in ingresso hanno ``data`` gia' convertita in ``datetime``
  (chi legge dal database scarta le righe con date illeggibili);
- quando un dato non e' calcolabile si restituisce ``None``, mai uno zero o un
  numero inventato: il report deve poter dire "non lo so".
"""

from .impostazioni import Impostazioni
from .utils import mediana


def _somma_scarichi(scarichi, oggi, giorni_min, giorni_max):
    """Somma le quantita' scaricate nella finestra [giorni_min, giorni_max).

    Intervallo semiaperto: i movimenti di oggi (eta' 0) rientrano nella prima
    finestra invece di sparire, e due finestre adiacenti non si sovrappongono
    sul confine.
    """
    totale = 0.0
    for m in scarichi:
        eta = (oggi - m["data"]).days
        if giorni_min <= eta < giorni_max:
            totale += m["quantita"]
    return totale


def _risultato_vuoto(n_movimenti=0):
    return {
        "n_movimenti": n_movimenti,
        "dati_sufficienti": False,
        "consumo_ultimi_30gg": 0.0,
        "consumo_30_60gg_fa": 0.0,
        "trend_consumo_pct": None,
        "velocita_consumo_giorno": 0.0,
        "velocita_prudente_giorno": 0.0,
        "giorni_autonomia_stimati": None,
        "giorni_autonomia_prudente": None,
        "tempo_restock_mediano_giorni": None,
        "lead_time_giorni": None,
        "ultimo_restock": None,
        "giorni_da_ultimo_restock": None,
        "punto_riordino": None,
        "da_riordinare": False,
        "quantita_da_ordinare": None,
        "giorni_entro_cui_ordinare": None,
    }


def analizza_movimenti(movimenti, giacenza_attuale, oggi, scorta_minima=0.0,
                       scorta_massima=0.0, imp=None):
    """Analisi completa di un singolo materiale.

    ``movimenti``: lista di dict con ``data`` (datetime), ``tipo``
    ('carico'/'scarico') e ``quantita`` (float).
    """
    imp = imp or Impostazioni()

    if not movimenti:
        return _risultato_vuoto()

    n_movimenti = len(movimenti)
    scarichi = [m for m in movimenti if m["tipo"] == "scarico"]
    carichi = [m for m in movimenti if m["tipo"] == "carico"]

    date_movimenti = [m["data"] for m in movimenti]
    giorni_storico = max((max(date_movimenti) - min(date_movimenti)).days, 1)

    finestra_1 = imp.giorni_finestra_recente
    finestra_2 = imp.giorni_finestra_confronto

    consumo_recente = _somma_scarichi(scarichi, oggi, 0, finestra_1)
    consumo_precedente = _somma_scarichi(scarichi, oggi, finestra_1, finestra_2)
    consumo_totale_finestra = consumo_recente + consumo_precedente

    # Le velocita' vanno divise per i giorni realmente coperti dallo storico,
    # non sempre per l'ampiezza nominale della finestra: un materiale visto per
    # la prima volta 20 giorni fa, diviso per 60, avrebbe una velocita'
    # sottostimata e quindi un'autonomia gonfiata.
    giorni_1 = min(finestra_1, giorni_storico)
    giorni_2 = min(finestra_2, giorni_storico)

    consumo_storico = sum(m["quantita"] for m in scarichi)
    velocita_storica = consumo_storico / giorni_storico if consumo_storico > 0 else 0.0

    velocita_media = (
        consumo_totale_finestra / giorni_2 if consumo_totale_finestra > 0 else velocita_storica
    )
    velocita_recente = consumo_recente / giorni_1 if consumo_recente > 0 else 0.0

    # Per gli allarmi si usa la velocita' piu' alta fra media e ultimo mese:
    # se il consumo sta accelerando, avvisare tardi costa piu' che avvisare
    # presto (rimanere senza materiale ferma la produzione).
    velocita_prudente = max(velocita_media, velocita_recente)

    trend_pct = (
        round((consumo_recente - consumo_precedente) / consumo_precedente * 100, 1)
        if consumo_precedente > 0
        else None
    )

    def autonomia(velocita):
        return round(giacenza_attuale / velocita, 1) if velocita > 0 else None

    giorni_autonomia = autonomia(velocita_media)
    giorni_autonomia_prudente = autonomia(velocita_prudente)

    # Cadenza di rifornimento: mediana degli intervalli fra carichi, piu'
    # robusta della media a un singolo riordino d'emergenza.
    date_carichi = sorted(m["data"] for m in carichi)
    intervalli = [
        (date_carichi[i] - date_carichi[i - 1]).days for i in range(1, len(date_carichi))
    ]
    # Due carichi lo stesso giorno (es. bolla divisa in due righe) non sono due
    # rifornimenti distinti: gli intervalli a zero giorni non contano.
    intervalli = [g for g in intervalli if g > 0]
    tempo_restock = round(mediana(intervalli), 1) if intervalli else None

    ultimo_restock = date_carichi[-1] if date_carichi else None
    giorni_da_ultimo_restock = (oggi - ultimo_restock).days if ultimo_restock else None

    dati_sufficienti = (
        n_movimenti >= imp.movimenti_minimi and giorni_storico >= imp.giorni_storico_minimi
    )

    if not dati_sufficienti:
        # Con pochi movimenti l'estrapolazione produce numeri assurdi (es.
        # "1328 giorni di autonomia" da un singolo scarico isolato): meglio
        # nessun numero che un numero fuorviante accanto al badge "dati
        # insufficienti".
        risultato = _risultato_vuoto(n_movimenti)
        risultato["consumo_ultimi_30gg"] = round(consumo_recente, 2)
        risultato["consumo_30_60gg_fa"] = round(consumo_precedente, 2)
        risultato["ultimo_restock"] = ultimo_restock
        risultato["giorni_da_ultimo_restock"] = giorni_da_ultimo_restock
        return risultato

    # --- Punto di riordino --------------------------------------------------
    # Quanto materiale deve restare a magazzino per coprire il tempo di
    # consegna senza rimanere a secco, piu' la scorta di sicurezza gia'
    # decisa nel gestionale (scorta minima).
    lead_time = tempo_restock if tempo_restock is not None else float(imp.lead_time_default_giorni)
    punto_riordino = None
    da_riordinare = False
    quantita_da_ordinare = None
    giorni_entro_cui_ordinare = None

    if velocita_prudente > 0:
        punto_riordino = round(velocita_prudente * lead_time + scorta_minima, 1)
        da_riordinare = giacenza_attuale <= punto_riordino
        if da_riordinare:
            # Si riporta la giacenza al livello obiettivo (scorta massima se
            # impostata, altrimenti il doppio del punto di riordino).
            livello_obiettivo = scorta_massima if scorta_massima > 0 else punto_riordino * 2
            quantita_da_ordinare = round(max(0.0, livello_obiettivo - giacenza_attuale), 1)
        if giorni_autonomia_prudente is not None:
            giorni_entro_cui_ordinare = max(0, int(round(giorni_autonomia_prudente - lead_time)))

    return {
        "n_movimenti": n_movimenti,
        "dati_sufficienti": True,
        "consumo_ultimi_30gg": round(consumo_recente, 2),
        "consumo_30_60gg_fa": round(consumo_precedente, 2),
        "trend_consumo_pct": trend_pct,
        "velocita_consumo_giorno": round(velocita_media, 3),
        "velocita_prudente_giorno": round(velocita_prudente, 3),
        "giorni_autonomia_stimati": giorni_autonomia,
        "giorni_autonomia_prudente": giorni_autonomia_prudente,
        "tempo_restock_mediano_giorni": tempo_restock,
        "lead_time_giorni": round(lead_time, 1),
        "ultimo_restock": ultimo_restock,
        "giorni_da_ultimo_restock": giorni_da_ultimo_restock,
        "punto_riordino": punto_riordino,
        "da_riordinare": da_riordinare,
        "quantita_da_ordinare": quantita_da_ordinare,
        "giorni_entro_cui_ordinare": giorni_entro_cui_ordinare,
    }


def stato_materiale(riga, imp=None):
    """Etichetta sintetica dello stato di un materiale.

    Vive qui (non nel JavaScript) perche' e' una regola di business: cosi' e'
    testabile e resta una sola definizione di "sotto scorta" / "da riordinare".
    Restituisce ``(codice, testo)``; il codice guida il colore nel report.
    """
    imp = imp or Impostazioni()
    if not riga.get("dati_sufficienti"):
        return ("", "Dati insufficienti")
    if riga.get("sotto_scorta"):
        return ("basso", "Sotto scorta")
    autonomia = riga.get("giorni_autonomia_prudente")
    if riga.get("da_riordinare"):
        return ("medio", "Da riordinare")
    if autonomia is not None and autonomia < imp.giorni_allarme_esaurimento:
        return ("medio", "In esaurimento")
    return ("ok", "OK")
