"""Cruscotto Aziendale RCS.

Genera un report HTML statico e autosufficiente leggendo in sola lettura il
database del gestionale RCS. Nessun server, nessun build step, nessuna
dipendenza oltre la libreria standard.

Uso:
    python3 cruscotto.py            # genera e apre il report nel browser
    python3 cruscotto.py --no-open  # genera senza aprire il browser
"""

import json
import os
import sys
import urllib.request
import webbrowser
from datetime import datetime
from string import Template

from config_db import ConfigDbError, apri_db_sola_lettura, risolvi_percorso_db

CARTELLA_PROGETTO = os.path.dirname(os.path.abspath(__file__))

CHARTJS_VERSION = "4.4.9"
CHARTJS_URL = "https://cdn.jsdelivr.net/npm/chart.js@" + CHARTJS_VERSION + "/dist/chart.umd.min.js"
CHARTJS_CACHE = os.path.join(CARTELLA_PROGETTO, "assets", "chart.umd.min.js")
DIMENSIONE_MINIMA_VALIDA = 100_000

# Soglie per considerare affidabile la stima su un materiale: sotto questi
# valori i movimenti registrati sono troppo pochi per calcolare velocita' di
# consumo/autonomia con un minimo di senso statistico.
SOGLIA_MOVIMENTI_MINIMI = 4
SOGLIA_GIORNI_STORICO_MINIMI = 14


def _num(valore):
    if valore is None:
        return 0.0
    try:
        return float(valore)
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# Estrazione dati
# ---------------------------------------------------------------------------


def estrai_preventivi(conn):
    cur = conn.execute(
        """
        SELECT id, data_creazione, nome_cliente, categoria,
               costo_totale_materiali, costi_accessori, tot_mano_opera,
               prezzo_cliente
        FROM preventivi
        WHERE preventivo_originale_id IS NULL
        ORDER BY data_creazione
        """
    )
    righe = []
    for r in cur.fetchall():
        prezzo = _num(r["prezzo_cliente"])
        costo_materiali = _num(r["costo_totale_materiali"])
        mano_opera = _num(r["tot_mano_opera"])
        costo_totale = costo_materiali + _num(r["costi_accessori"]) + mano_opera
        margine_pct = round((prezzo - costo_totale) / prezzo * 100, 2) if prezzo > 0 else 0.0
        righe.append(
            {
                "id": r["id"],
                "data": r["data_creazione"],
                "cliente": r["nome_cliente"] or "",
                "categoria": r["categoria"] or "",
                "prezzo": round(prezzo, 2),
                "costo_materiali": round(costo_materiali, 2),
                "mano_opera": round(mano_opera, 2),
                "margine_pct": margine_pct,
            }
        )
    return righe


def _analizza_movimenti(movimenti, giacenza_attuale, oggi):
    """Calcola velocita' di consumo, tempo di restock e autonomia stimata per
    un singolo materiale a partire dalla sua lista di movimenti (ordinata).

    E' la parte "intelligente" richiesta: quanto si consuma, ogni quanto si
    fa restock, quanto durera' ancora la scorta al ritmo attuale. Quando i
    movimenti sono troppo pochi (`dati_sufficienti=False`) il report deve
    dirlo esplicitamente invece di inventare una stima.
    """
    n_movimenti = len(movimenti)
    if not movimenti:
        return {
            "n_movimenti": 0,
            "dati_sufficienti": False,
            "consumo_ultimi_30gg": 0.0,
            "trend_consumo_pct": None,
            "velocita_consumo_giorno": 0.0,
            "giorni_autonomia_stimati": None,
            "tempo_medio_restock_giorni": None,
            "ultimo_restock": None,
            "giorni_da_ultimo_restock": None,
        }

    scarichi = [m for m in movimenti if m["tipo"] == "scarico"]
    carichi = [m for m in movimenti if m["tipo"] == "carico"]

    date_movimenti = [datetime.fromisoformat(m["data"]) for m in movimenti]
    primo, ultimo = min(date_movimenti), max(date_movimenti)
    giorni_storico = max((ultimo - primo).days, 1)

    def scarico_entro(giorni_min, giorni_max):
        totale = 0.0
        for m in scarichi:
            eta = (oggi - datetime.fromisoformat(m["data"])).days
            if giorni_min < eta <= giorni_max:
                totale += m["quantita"]
        return totale

    consumo_totale = sum(m["quantita"] for m in scarichi)
    velocita_storica = consumo_totale / giorni_storico if consumo_totale > 0 else 0.0

    consumo_30 = scarico_entro(0, 30)
    consumo_30_60 = scarico_entro(30, 60)
    consumo_60 = consumo_30 + consumo_30_60

    velocita_recente = (consumo_60 / 60.0) if consumo_60 > 0 else velocita_storica

    trend_pct = (
        round((consumo_30 - consumo_30_60) / consumo_30_60 * 100, 1)
        if consumo_30_60 > 0
        else None
    )

    giorni_autonomia = (
        round(giacenza_attuale / velocita_recente, 1) if velocita_recente > 0 else None
    )

    date_carichi = sorted(datetime.fromisoformat(m["data"]) for m in carichi)
    if len(date_carichi) >= 2:
        delta = [
            (date_carichi[i] - date_carichi[i - 1]).days for i in range(1, len(date_carichi))
        ]
        tempo_medio_restock = round(sum(delta) / len(delta), 1)
    else:
        tempo_medio_restock = None

    ultimo_restock = date_carichi[-1].isoformat() if date_carichi else None
    giorni_da_ultimo_restock = (oggi - date_carichi[-1]).days if date_carichi else None

    dati_sufficienti = n_movimenti >= SOGLIA_MOVIMENTI_MINIMI and giorni_storico >= SOGLIA_GIORNI_STORICO_MINIMI

    return {
        "n_movimenti": n_movimenti,
        "dati_sufficienti": dati_sufficienti,
        "consumo_ultimi_30gg": round(consumo_30, 2),
        "trend_consumo_pct": trend_pct,
        "velocita_consumo_giorno": round(velocita_recente, 3),
        "giorni_autonomia_stimati": giorni_autonomia,
        "tempo_medio_restock_giorni": tempo_medio_restock,
        "ultimo_restock": ultimo_restock,
        "giorni_da_ultimo_restock": giorni_da_ultimo_restock,
    }


def estrai_magazzino(conn, oggi=None):
    """Una riga per materiale: giacenza attuale (sommata sui fornitori, non
    dalla colonna materiali.giacenza che nel DB del gestionale resta sempre a
    0), soglie di scorta, e l'analisi di consumo/restock/autonomia.
    """
    if oggi is None:
        oggi = datetime.now()

    cur = conn.execute(
        """
        SELECT m.id, m.nome, m.prezzo, m.scorta_minima, m.scorta_massima,
               m.capacita_magazzino, c.capacita_magazzino AS cat_capacita
        FROM materiali m
        LEFT JOIN categorie_materiale c ON c.id = m.categoria_id
        ORDER BY m.nome
        """
    )
    materiali = [dict(r) for r in cur.fetchall()]

    cur = conn.execute(
        "SELECT materiale_id, fornitore_nome, prezzo_fornitore, giacenza FROM materiale_fornitori"
    )
    fornitori_per_materiale = {}
    for r in cur.fetchall():
        fornitori_per_materiale.setdefault(r["materiale_id"], []).append(dict(r))

    cur = conn.execute(
        "SELECT materiale_id, tipo, quantita, data FROM movimenti_magazzino ORDER BY materiale_id, data"
    )
    movimenti_per_materiale = {}
    for r in cur.fetchall():
        movimenti_per_materiale.setdefault(r["materiale_id"], []).append(dict(r))

    righe = []
    for m in materiali:
        forn = fornitori_per_materiale.get(m["id"], [])
        giacenza = round(sum(_num(f["giacenza"]) for f in forn), 2)
        scorta_minima = round(_num(m["scorta_minima"]), 2)
        scorta_massima = round(_num(m["scorta_massima"]), 2)
        capacita = _num(m["capacita_magazzino"]) or _num(m["cat_capacita"]) or scorta_massima
        prezzo_fornitore_medio = (
            round(sum(_num(f["prezzo_fornitore"]) for f in forn) / len(forn), 2) if forn else 0.0
        )

        movimenti = movimenti_per_materiale.get(m["id"], [])
        analisi = _analizza_movimenti(movimenti, giacenza, oggi)

        riga = {
            "materiale_id": m["id"],
            "nome": m["nome"],
            "giacenza": giacenza,
            "scorta_minima": scorta_minima,
            "scorta_massima": scorta_massima,
            "capacita_magazzino": round(capacita, 2),
            "prezzo_vendita": round(_num(m["prezzo"]), 2),
            "prezzo_fornitore_medio": prezzo_fornitore_medio,
            "n_fornitori": len(forn),
            "sotto_scorta": scorta_minima > 0 and giacenza < scorta_minima,
        }
        riga.update(analisi)
        righe.append(riga)
    return righe


def estrai_confronto_fornitori(conn):
    cur = conn.execute(
        """
        SELECT mf.materiale_id, m.nome AS materiale_nome, mf.fornitore_nome,
               mf.prezzo_fornitore, mf.giacenza
        FROM materiale_fornitori mf
        JOIN materiali m ON m.id = mf.materiale_id
        ORDER BY m.nome, mf.prezzo_fornitore
        """
    )
    per_materiale = {}
    for r in cur.fetchall():
        gruppo = per_materiale.setdefault(r["materiale_id"], {"nome": r["materiale_nome"], "fornitori": []})
        gruppo["fornitori"].append(
            {
                "fornitore": r["fornitore_nome"],
                "prezzo": round(_num(r["prezzo_fornitore"]), 2),
                "giacenza": round(_num(r["giacenza"]), 2),
            }
        )

    righe = []
    for dati in per_materiale.values():
        if len(dati["fornitori"]) < 2:
            continue
        prezzi = [f["prezzo"] for f in dati["fornitori"] if f["prezzo"] > 0]
        risparmio_pct = round((max(prezzi) - min(prezzi)) / max(prezzi) * 100, 1) if len(prezzi) >= 2 else 0.0
        righe.append({"nome": dati["nome"], "fornitori": dati["fornitori"], "risparmio_pct": risparmio_pct})
    righe.sort(key=lambda r: r["risparmio_pct"], reverse=True)
    return righe


def estrai_movimenti(conn, limite=40):
    cur = conn.execute(
        """
        SELECT mm.data, mm.tipo, mm.quantita, mm.fornitore_nome, mm.note, m.nome AS materiale_nome
        FROM movimenti_magazzino mm
        JOIN materiali m ON m.id = mm.materiale_id
        ORDER BY mm.data DESC
        LIMIT ?
        """,
        (limite,),
    )
    return [
        {
            "data": r["data"],
            "tipo": r["tipo"],
            "quantita": round(_num(r["quantita"]), 2),
            "fornitore": r["fornitore_nome"] or "",
            "materiale": r["materiale_nome"],
            "note": r["note"] or "",
        }
        for r in cur.fetchall()
    ]


def estrai_manodopera(conn):
    cur = conn.execute(
        """
        SELECT COALESCE(SUM(minuti_taglio),0) taglio,
               COALESCE(SUM(minuti_avvolgimento),0) avvolgimento,
               COALESCE(SUM(minuti_pulizia),0) pulizia,
               COALESCE(SUM(minuti_rettifica),0) rettifica,
               COALESCE(SUM(minuti_imballaggio),0) imballaggio
        FROM preventivi
        WHERE preventivo_originale_id IS NULL
        """
    )
    r = cur.fetchone()
    fasi = {
        "Taglio": _num(r["taglio"]),
        "Avvolgimento": _num(r["avvolgimento"]),
        "Pulizia": _num(r["pulizia"]),
        "Rettifica": _num(r["rettifica"]),
        "Imballaggio": _num(r["imballaggio"]),
    }
    return [{"fase": nome, "minuti": round(v, 1)} for nome, v in fasi.items() if v > 0]


def estrai_materiali_piu_usati(conn, limite=10):
    cur = conn.execute(
        """
        SELECT m.nome, COALESCE(SUM(mm.quantita), 0) consumo
        FROM movimenti_magazzino mm
        JOIN materiali m ON m.id = mm.materiale_id
        WHERE mm.tipo = 'scarico'
        GROUP BY m.id
        ORDER BY consumo DESC
        LIMIT ?
        """,
        (limite,),
    )
    return [{"nome": r["nome"], "consumo": round(_num(r["consumo"]), 2)} for r in cur.fetchall()]


def estrai_clienti(conn, limite=10):
    cur = conn.execute(
        """
        SELECT nome_cliente, COALESCE(SUM(prezzo_cliente), 0) valore, COUNT(*) n_preventivi
        FROM preventivi
        WHERE preventivo_originale_id IS NULL AND nome_cliente != ''
        GROUP BY nome_cliente
        ORDER BY valore DESC
        LIMIT ?
        """,
        (limite,),
    )
    return [
        {"cliente": r["nome_cliente"], "valore": round(_num(r["valore"]), 2), "n_preventivi": r["n_preventivi"]}
        for r in cur.fetchall()
    ]


def estrai_ricarico(conn):
    cur = conn.execute(
        """
        SELECT m.nome, m.prezzo,
               (SELECT AVG(prezzo_fornitore) FROM materiale_fornitori mf WHERE mf.materiale_id = m.id) prezzo_fornitore
        FROM materiali m
        ORDER BY m.nome
        """
    )
    righe = []
    for r in cur.fetchall():
        prezzo = _num(r["prezzo"])
        costo = _num(r["prezzo_fornitore"])
        if costo <= 0:
            continue
        righe.append(
            {
                "nome": r["nome"],
                "prezzo_vendita": round(prezzo, 2),
                "prezzo_fornitore": round(costo, 2),
                "ricarico_pct": round((prezzo - costo) / costo * 100, 1),
            }
        )
    righe.sort(key=lambda r: r["ricarico_pct"])
    return righe


def carica_notiziario():
    percorso = os.path.join(CARTELLA_PROGETTO, "notiziario_ultimo.json")
    if not os.path.isfile(percorso):
        return None
    try:
        with open(percorso, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def ottieni_chartjs():
    """(contenuto_o_None, usa_cdn). Cache locale -> download -> ripiego CDN."""
    if os.path.isfile(CHARTJS_CACHE) and os.path.getsize(CHARTJS_CACHE) > DIMENSIONE_MINIMA_VALIDA:
        with open(CHARTJS_CACHE, "r", encoding="utf-8") as f:
            return f.read(), False
    try:
        with urllib.request.urlopen(CHARTJS_URL, timeout=10) as resp:
            contenuto = resp.read().decode("utf-8")
        if len(contenuto) > DIMENSIONE_MINIMA_VALIDA:
            os.makedirs(os.path.dirname(CHARTJS_CACHE), exist_ok=True)
            with open(CHARTJS_CACHE, "w", encoding="utf-8") as f:
                f.write(contenuto)
            return contenuto, False
    except Exception:
        pass
    return None, True


# ---------------------------------------------------------------------------
# Template HTML (CSS + JS inline, vedi HANDOFF.md per le regole del design)
# ---------------------------------------------------------------------------

HTML_TEMPLATE = Template(r"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>RCS · Cruscotto Aziendale</title>
<style>
:root {
  --bg: #0d1117;
  --card: #151c25;
  --card2: #1a222d;
  --bordo: #25303d;
  --testo: #e6edf3;
  --muto: #8b97a5;
  --accento: #f0b429;
  --teal: #43c6b9;
  --verde: #4ec97b;
  --rosso: #ef6a6a;
  --blu: #6aa9ef;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background-color: var(--bg);
  background-image:
    repeating-linear-gradient(45deg, rgba(255,255,255,.014) 0px, rgba(255,255,255,.014) 1px, transparent 1px, transparent 12px),
    repeating-linear-gradient(-45deg, rgba(255,255,255,.014) 0px, rgba(255,255,255,.014) 1px, transparent 1px, transparent 12px);
  color: var(--testo);
  font-family: "Segoe UI", system-ui, -apple-system, sans-serif;
  line-height: 1.5;
}
.contenitore { max-width: 1180px; margin: 0 auto; padding: 24px 20px 64px; }
.num, .valore, .kpi .valore { font-variant-numeric: tabular-nums; }

header.testata {
  display: flex; align-items: center; justify-content: space-between;
  flex-wrap: wrap; gap: 12px; padding: 18px 0 28px;
}
.logo { font-size: 20px; font-weight: 600; letter-spacing: .3px; }
.logo span { color: var(--accento); }
.meta-testata { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
.data-generazione { color: var(--muto); font-size: 13px; }

.selettore-periodo { display: flex; gap: 6px; background: var(--card); border: 1px solid var(--bordo); border-radius: 999px; padding: 4px; }
.pill {
  border: none; background: transparent; color: var(--muto); font-size: 13px;
  padding: 7px 16px; border-radius: 999px; cursor: pointer; transition: .2s;
  font-family: inherit;
}
.pill.attiva { background: var(--accento); color: #14100a; font-weight: 600; }
.pill:not(.attiva):hover { color: var(--testo); }

.sezione {
  background: var(--card); border: 1px solid var(--bordo); border-radius: 16px;
  padding: 22px 24px; margin-bottom: 20px;
  opacity: 0; transform: translateY(14px);
  transition: opacity .55s ease, transform .55s ease;
}
.sezione.visibile { opacity: 1; transform: translateY(0); }
.sezione h2 {
  margin: 0 0 16px; font-size: 16px; font-weight: 600; display: flex; align-items: center; gap: 10px;
}
.sezione h2::before { content: ""; width: 3px; height: 16px; background: var(--accento); border-radius: 999px; display: inline-block; }
.nota-periodo { color: var(--muto); font-weight: 400; font-size: 12.5px; }

.griglia-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
@media (max-width: 900px) { .griglia-2 { grid-template-columns: 1fr; } }

.griglia-kpi { display: grid; grid-template-columns: repeat(6, 1fr); gap: 14px; margin-bottom: 20px; }
@media (max-width: 1000px) { .griglia-kpi { grid-template-columns: repeat(3, 1fr); } }
@media (max-width: 620px) { .griglia-kpi { grid-template-columns: repeat(2, 1fr); } }

.kpi {
  background: var(--card); border: 1px solid var(--bordo); border-radius: 14px; padding: 16px;
  opacity: 0; transform: translateY(10px); transition: opacity .5s ease, transform .5s ease;
}
.kpi.visibile { opacity: 1; transform: translateY(0); }
.kpi .etichetta { color: var(--muto); font-size: 12px; margin-bottom: 8px; }
.kpi .valore { font-size: 24px; font-weight: 700; }
.kpi .delta { font-size: 12px; margin-top: 6px; display: inline-flex; align-items: center; gap: 4px; }
.kpi .delta.su { color: var(--verde); }
.kpi .delta.giu { color: var(--rosso); }
.kpi .delta.neutro { color: var(--muto); }

.blocchi-sintesi { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
@media (max-width: 900px) { .blocchi-sintesi { grid-template-columns: 1fr; } }
.blocco-sintesi { background: var(--card2); border: 1px solid var(--bordo); border-radius: 12px; padding: 16px; }
.blocco-sintesi h3 { margin: 0 0 10px; font-size: 13.5px; display: flex; align-items: center; gap: 8px; }
.blocco-sintesi.forza h3::before { content: "\25B2"; color: var(--verde); font-size: 11px; }
.blocco-sintesi.attenzione h3::before { content: "\25CF"; color: var(--accento); font-size: 11px; }
.blocco-sintesi.cambi h3::before { content: "\25C6"; color: var(--teal); font-size: 11px; }
.blocco-sintesi ul { margin: 0; padding-left: 18px; font-size: 13px; color: var(--testo); }
.blocco-sintesi li { margin-bottom: 6px; }
.blocco-sintesi li:last-child { margin-bottom: 0; }
.blocco-sintesi .vuoto { color: var(--muto); font-style: italic; font-size: 13px; }

.contenitore-grafico { position: relative; width: 100%; }

table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { text-align: left; padding: 9px 10px; border-bottom: 1px solid var(--bordo); }
th { color: var(--muto); font-weight: 600; font-size: 11.5px; text-transform: uppercase; letter-spacing: .4px; }
td.num, th.num { text-align: right; }
tbody tr:last-child td { border-bottom: none; }
.tabella-scroll { overflow-x: auto; }

.tag { display: inline-block; padding: 3px 10px; border-radius: 999px; font-size: 11.5px; font-weight: 600; }
.tag.ok { background: rgba(78,201,123,.14); color: var(--verde); }
.tag.basso { background: rgba(239,106,106,.14); color: var(--rosso); }
.tag.medio { background: rgba(240,180,41,.14); color: var(--accento); }
.tag.carico { background: rgba(106,169,239,.14); color: var(--blu); }
.tag.scarico { background: rgba(139,151,165,.14); color: var(--muto); }
.testo-muto { color: var(--muto); }

.notizia { border-left: 3px solid var(--teal); background: var(--card2); border-radius: 0 10px 10px 0; padding: 12px 16px; margin-bottom: 12px; }
.notizia:last-child { margin-bottom: 0; }
.notizia .fonte { color: var(--muto); font-size: 11.5px; margin-top: 6px; }
.rilevanza { color: var(--accento); font-style: italic; font-size: 12.5px; margin-top: 6px; }
.suggerimento { border: 1px dashed var(--bordo); border-radius: 10px; padding: 14px 16px; color: var(--muto); font-size: 13px; }

footer.piede { text-align: center; color: var(--muto); font-size: 12px; padding: 24px 0 6px; }
</style>
</head>
<body>
<div class="contenitore">

  <header class="testata">
    <div class="logo">RCS <span>&middot;</span> Cruscotto Aziendale</div>
    <div class="meta-testata">
      <span class="data-generazione">Generato il $DATA_GENERAZIONE</span>
      <div class="selettore-periodo" id="selettorePeriodo">
        <button class="pill" data-periodo="mese">Mese</button>
        <button class="pill" data-periodo="trimestre">Trimestre</button>
        <button class="pill" data-periodo="anno">Anno</button>
      </div>
    </div>
  </header>

  <div class="griglia-kpi" id="grigliaKpi"></div>

  <div class="sezione" id="sezioneSintesi">
    <h2>In sintesi <span class="nota-periodo">rispetto al periodo precedente</span></h2>
    <div class="blocchi-sintesi" id="blocchiSintesi"></div>
  </div>

  <div class="sezione">
    <h2>Andamento preventivi <span class="nota-periodo" id="notaAndamento"></span></h2>
    <div class="contenitore-grafico" style="height:300px;"><canvas id="graficoAndamento"></canvas></div>
  </div>

  <div class="griglia-2">
    <div class="sezione">
      <h2>Margine medio <span class="nota-periodo">per periodo</span></h2>
      <div class="contenitore-grafico" style="height:260px;"><canvas id="graficoMargini"></canvas></div>
    </div>
    <div class="sezione">
      <h2>Composizione costi <span class="nota-periodo">per periodo</span></h2>
      <div class="contenitore-grafico" style="height:260px;"><canvas id="graficoComposizione"></canvas></div>
    </div>
  </div>

  <div class="sezione">
    <h2>Magazzino &middot; analisi intelligente <span class="nota-periodo">storico completo, consumo e riordino</span></h2>
    <div class="griglia-2" style="margin-bottom:20px;">
      <div class="contenitore-grafico" style="height:$ALTEZZA_GRAFICO_MAGAZZINOpx;"><canvas id="graficoGiacenze"></canvas></div>
      <div class="contenitore-grafico" style="height:$ALTEZZA_GRAFICO_AUTONOMIApx;"><canvas id="graficoAutonomia"></canvas></div>
    </div>
    <div class="tabella-scroll" style="margin-bottom:18px;">
      <table>
        <thead>
          <tr>
            <th>Materiale</th><th class="num">Giacenza</th><th class="num">Scorta min/max</th>
            <th class="num">Consumo 30gg</th><th class="num">Velocit&agrave; /giorno</th>
            <th class="num">Autonomia stimata</th><th class="num">Restock medio</th>
            <th>Ultimo restock</th><th>Stato</th>
          </tr>
        </thead>
        <tbody id="corpoTabellaMagazzino"></tbody>
      </table>
    </div>
    <div class="blocchi-sintesi" id="blocchiConsigliMagazzino"></div>
  </div>

  <div class="griglia-2">
    <div class="sezione">
      <h2>Manodopera per fase <span class="nota-periodo">storico completo</span></h2>
      <div class="contenitore-grafico" style="height:260px;"><canvas id="graficoManodopera"></canvas></div>
    </div>
    <div class="sezione">
      <h2>Materiali pi&ugrave; usati <span class="nota-periodo">storico completo</span></h2>
      <div class="contenitore-grafico" style="height:260px;"><canvas id="graficoMaterialiUsati"></canvas></div>
    </div>
  </div>

  <div class="sezione">
    <h2>Clienti per valore <span class="nota-periodo">storico completo</span></h2>
    <div class="contenitore-grafico" style="height:280px;"><canvas id="graficoClienti"></canvas></div>
  </div>

  <div class="sezione">
    <h2>Ricarico per materiale <span class="nota-periodo">vendita vs prezzo fornitore</span></h2>
    <div class="tabella-scroll">
      <table>
        <thead><tr><th>Materiale</th><th class="num">Prezzo vendita</th><th class="num">Prezzo fornitore</th><th class="num">Ricarico</th></tr></thead>
        <tbody id="corpoTabellaRicarico"></tbody>
      </table>
    </div>
  </div>

  <div class="sezione" id="sezioneFornitori" style="display:none;">
    <h2>Confronto fornitori <span class="nota-periodo">materiali con pi&ugrave; di un fornitore</span></h2>
    <div class="tabella-scroll">
      <table>
        <thead><tr><th>Materiale</th><th>Fornitore</th><th class="num">Prezzo</th><th class="num">Giacenza</th><th class="num">Risparmio possibile</th></tr></thead>
        <tbody id="corpoTabellaFornitori"></tbody>
      </table>
    </div>
  </div>

  <div class="sezione">
    <h2>Ultimi movimenti di magazzino</h2>
    <div class="tabella-scroll">
      <table>
        <thead><tr><th>Data</th><th>Materiale</th><th>Tipo</th><th class="num">Quantit&agrave;</th><th>Fornitore</th><th>Note</th></tr></thead>
        <tbody id="corpoTabellaMovimenti"></tbody>
      </table>
    </div>
  </div>

  <div class="sezione" id="sezioneNotiziario">
    <h2>Notiziario di settore</h2>
    <div id="contenutoNotiziario"></div>
  </div>

  <footer class="piede">Cruscotto Aziendale RCS &middot; report generato in locale, nessun dato lascia il computer</footer>
</div>

$TAG_CHARTJS
<script>
var DATI = $JSON_DATI;
var graficiAttivi = {};
var periodoCorrente = 'mese';

function colore(nome) {
  return getComputedStyle(document.documentElement).getPropertyValue('--' + nome).trim();
}

function esc(s) {
  return String(s === undefined || s === null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function formattaEuro(v) {
  return '€ ' + (v || 0).toLocaleString('it-IT', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function formattaNumero(v, decimali) {
  return (v || 0).toLocaleString('it-IT', { minimumFractionDigits: decimali || 0, maximumFractionDigits: decimali || 0 });
}
function formattaData(iso) {
  if (!iso) return '—';
  var d = new Date(iso);
  var gg = String(d.getDate()).padStart(2, '0');
  var mm = String(d.getMonth() + 1).padStart(2, '0');
  return gg + '/' + mm + '/' + d.getFullYear();
}

function animaNumero(elemento, valoreFinale, formattatore) {
  var durata = 750;
  var inizio = null;
  var partenza = 0;
  function passo(timestamp) {
    if (inizio === null) inizio = timestamp;
    var f = Math.min((timestamp - inizio) / durata, 1);
    var eased = 1 - Math.pow(1 - f, 3);
    var valoreCorrente = partenza + (valoreFinale - partenza) * eased;
    elemento.textContent = formattatore(valoreCorrente);
    if (f < 1) requestAnimationFrame(passo);
  }
  requestAnimationFrame(passo);
}

function opzioniBase() {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 750, easing: 'easeOutQuart' },
    plugins: {
      legend: { labels: { color: colore('muto'), font: { family: '"Segoe UI", system-ui, sans-serif', size: 12 } } },
      tooltip: { titleFont: { family: '"Segoe UI", system-ui, sans-serif' }, bodyFont: { family: '"Segoe UI", system-ui, sans-serif' } }
    }
  };
}
function asseX(extra) {
  var base = { grid: { color: colore('bordo') }, ticks: { color: colore('muto') } };
  if (extra) for (var k in extra) base[k] = extra[k];
  return base;
}
function asseY(extra) {
  var base = { grid: { color: colore('bordo') }, ticks: { color: colore('muto') } };
  if (extra) for (var k in extra) base[k] = extra[k];
  return base;
}

function datiGrafico(id, tipo, dati, opzioni) {
  var canvas = document.getElementById(id);
  if (!canvas || typeof Chart === 'undefined') return null;
  if (graficiAttivi[id]) graficiAttivi[id].destroy();
  var grafico = new Chart(canvas.getContext('2d'), { type: tipo, data: dati, options: opzioni });
  graficiAttivi[id] = grafico;
  return grafico;
}

// --- Periodo: chiavi e aggregazione ------------------------------------

function chiavePeriodo(dataIso, periodo) {
  var d = new Date(dataIso);
  var anno = d.getFullYear();
  var mese = d.getMonth();
  if (periodo === 'mese') {
    return anno + '-' + String(mese + 1).padStart(2, '0');
  }
  if (periodo === 'trimestre') {
    return anno + '-T' + (Math.floor(mese / 3) + 1);
  }
  return '' + anno;
}

function etichettaPeriodo(chiave, periodo) {
  if (periodo === 'mese') {
    var parti = chiave.split('-');
    var mesi = ['Gen', 'Feb', 'Mar', 'Apr', 'Mag', 'Giu', 'Lug', 'Ago', 'Set', 'Ott', 'Nov', 'Dic'];
    return mesi[parseInt(parti[1], 10) - 1] + ' ' + parti[0];
  }
  if (periodo === 'trimestre') return chiave.replace('-T', ' · T');
  return chiave;
}

function periodoAdiacente(chiave, periodo, offset) {
  if (periodo === 'anno') return '' + (parseInt(chiave, 10) + offset);
  if (periodo === 'trimestre') {
    var parti = chiave.split('-T');
    var totale = parseInt(parti[0], 10) * 4 + (parseInt(parti[1], 10) - 1) + offset;
    return Math.floor(totale / 4) + '-T' + ((totale % 4) + 1);
  }
  var parti2 = chiave.split('-');
  var totale2 = parseInt(parti2[0], 10) * 12 + (parseInt(parti2[1], 10) - 1) + offset;
  var nuovoMese = (totale2 % 12) + 1;
  return Math.floor(totale2 / 12) + '-' + String(nuovoMese).padStart(2, '0');
}

function aggrega(periodo) {
  var gruppi = {};
  DATI.preventivi.forEach(function (p) {
    var chiave = chiavePeriodo(p.data, periodo);
    if (!gruppi[chiave]) {
      gruppi[chiave] = { chiave: chiave, n: 0, valore: 0, margineSomma: 0, costoMateriali: 0, manoOpera: 0 };
    }
    var g = gruppi[chiave];
    g.n += 1;
    g.valore += p.prezzo;
    g.margineSomma += p.margine_pct;
    g.costoMateriali += p.costo_materiali;
    g.manoOpera += p.mano_opera;
  });
  return Object.keys(gruppi).sort().map(function (k) {
    var g = gruppi[k];
    return {
      chiave: k, etichetta: etichettaPeriodo(k, periodo), n: g.n, valore: g.valore,
      margineMedio: g.n > 0 ? g.margineSomma / g.n : 0,
      prezzoMedio: g.n > 0 ? g.valore / g.n : 0,
      costoMateriali: g.costoMateriali, manoOpera: g.manoOpera
    };
  });
}

function calcolaDelta(attuale, precedente) {
  if (precedente === null || precedente === undefined || precedente === 0) return null;
  return ((attuale - precedente) / Math.abs(precedente)) * 100;
}

function kpiCard(id, etichetta, valoreTesto, valoreNumerico, formattatore, deltaPct) {
  var classeDelta = 'neutro';
  var freccia = '—';
  var testoDelta = 'nessun confronto disponibile';
  if (deltaPct !== null && deltaPct !== undefined && isFinite(deltaPct)) {
    classeDelta = deltaPct > 0.5 ? 'su' : (deltaPct < -0.5 ? 'giu' : 'neutro');
    freccia = deltaPct > 0.5 ? '▲' : (deltaPct < -0.5 ? '▼' : '—');
    testoDelta = (deltaPct > 0 ? '+' : '') + formattaNumero(deltaPct, 1) + '% vs periodo prec.';
  }
  return '<div class="kpi" data-kpi="' + id + '">' +
    '<div class="etichetta">' + esc(etichetta) + '</div>' +
    '<div class="valore" id="valoreKpi' + id + '">0</div>' +
    '<div class="delta ' + classeDelta + '">' + freccia + ' <span>' + testoDelta + '</span></div>' +
    '</div>';
}

function disegnaGrigliaKpi(corrente, precedente, capitaleMagazzino, materialiSottoScorta) {
  var contenitore = document.getElementById('grigliaKpi');
  contenitore.innerHTML =
    kpiCard('NPrev', 'N. preventivi', '', corrente.n, null, calcolaDelta(corrente.n, precedente ? precedente.n : null)) +
    kpiCard('Valore', 'Valore preventivato', '', corrente.valore, null, calcolaDelta(corrente.valore, precedente ? precedente.valore : null)) +
    kpiCard('Margine', 'Margine medio', '', corrente.margineMedio, null, calcolaDelta(corrente.margineMedio, precedente ? precedente.margineMedio : null)) +
    kpiCard('Prezzo', 'Prezzo medio', '', corrente.prezzoMedio, null, calcolaDelta(corrente.prezzoMedio, precedente ? precedente.prezzoMedio : null)) +
    kpiCard('Capitale', 'Capitale a magazzino', '', capitaleMagazzino, null, null) +
    kpiCard('SottoScorta', 'Materiali sotto scorta', '', materialiSottoScorta, null, null);

  animaNumero(document.getElementById('valoreKpiNPrev'), corrente.n, function (v) { return formattaNumero(v, 0); });
  animaNumero(document.getElementById('valoreKpiValore'), corrente.valore, formattaEuro);
  animaNumero(document.getElementById('valoreKpiMargine'), corrente.margineMedio, function (v) { return formattaNumero(v, 1) + '%'; });
  animaNumero(document.getElementById('valoreKpiPrezzo'), corrente.prezzoMedio, formattaEuro);
  animaNumero(document.getElementById('valoreKpiCapitale'), capitaleMagazzino, formattaEuro);
  animaNumero(document.getElementById('valoreKpiSottoScorta'), materialiSottoScorta, function (v) { return formattaNumero(v, 0); });

  document.querySelectorAll('.kpi').forEach(function (el) { el.classList.add('visibile'); });
}

// --- In sintesi (generale) ----------------------------------------------

function bloccoSintesi(titolo, classe, righe) {
  var lista = righe.length
    ? '<ul>' + righe.map(function (r) { return '<li>' + r + '</li>'; }).join('') + '</ul>'
    : '<div class="vuoto">Nessuna nota per questo periodo.</div>';
  return '<div class="blocco-sintesi ' + classe + '"><h3>' + titolo + '</h3>' + lista + '</div>';
}

function disegnaSintesi(corrente, precedente) {
  var forza = [], attenzione = [], cambi = [];

  if (precedente) {
    var deltaValore = calcolaDelta(corrente.valore, precedente.valore);
    var deltaMargine = corrente.margineMedio - precedente.margineMedio;
    if (deltaValore !== null && deltaValore > 5) forza.push('Il valore preventivato è salito del ' + formattaNumero(deltaValore, 1) + '% rispetto al periodo precedente.');
    if (deltaMargine > 1) forza.push('Il margine medio è migliorato di ' + formattaNumero(deltaMargine, 1) + ' punti percentuali.');
    if (deltaValore !== null && deltaValore < -5) attenzione.push('Il valore preventivato è sceso del ' + formattaNumero(Math.abs(deltaValore), 1) + '% rispetto al periodo precedente.');
    if (deltaMargine < -1) attenzione.push('Il margine medio è peggiorato di ' + formattaNumero(Math.abs(deltaMargine), 1) + ' punti percentuali.');
  } else {
    cambi.push('Ancora nessun periodo precedente da confrontare.');
  }

  var sottoScorta = DATI.magazzino.filter(function (m) { return m.sotto_scorta; });
  if (sottoScorta.length > 0) {
    attenzione.push(sottoScorta.length + ' material' + (sottoScorta.length === 1 ? 'e' : 'i') + ' sotto la scorta minima: ' + sottoScorta.slice(0, 3).map(function (m) { return esc(m.nome); }).join(', ') + (sottoScorta.length > 3 ? '...' : '') + '.');
  } else {
    forza.push('Nessun materiale è sotto la scorta minima al momento.');
  }

  var senzaDati = DATI.magazzino.filter(function (m) { return !m.dati_sufficienti; });
  if (senzaDati.length > 0) {
    cambi.push(senzaDati.length + ' material' + (senzaDati.length === 1 ? 'e non ha' : 'i non hanno') + ' ancora abbastanza storico per una stima di consumo affidabile.');
  }

  document.getElementById('blocchiSintesi').innerHTML =
    bloccoSintesi('Punti di forza', 'forza', forza) +
    bloccoSintesi('Punti di attenzione', 'attenzione', attenzione) +
    bloccoSintesi('Cambiamenti', 'cambi', cambi);
}

// --- Grafici periodo-dipendenti -------------------------------------------

function disegnaAndamento(serie) {
  document.getElementById('notaAndamento').textContent = 'per ' + periodoCorrente;
  datiGrafico('graficoAndamento', 'bar', {
    labels: serie.map(function (g) { return g.etichetta; }),
    datasets: [
      { type: 'bar', label: 'N. preventivi', data: serie.map(function (g) { return g.n; }), backgroundColor: colore('blu'), yAxisID: 'y', borderRadius: 4 },
      { type: 'line', label: 'Valore (€)', data: serie.map(function (g) { return g.valore; }), borderColor: colore('accento'), backgroundColor: colore('accento'), yAxisID: 'y1', tension: .3 }
    ]
  }, Object.assign(opzioniBase(), {
    scales: {
      x: asseX(), y: asseY({ position: 'left', title: { display: true, text: 'N. preventivi', color: colore('muto') } }),
      y1: asseY({ position: 'right', grid: { display: false }, title: { display: true, text: 'Valore (€)', color: colore('muto') } })
    }
  }));
}

function disegnaMargini(serie) {
  datiGrafico('graficoMargini', 'line', {
    labels: serie.map(function (g) { return g.etichetta; }),
    datasets: [{ label: 'Margine medio %', data: serie.map(function (g) { return g.margineMedio; }), borderColor: colore('verde'), backgroundColor: colore('verde'), tension: .3, fill: false }]
  }, Object.assign(opzioniBase(), { scales: { x: asseX(), y: asseY() } }));
}

function disegnaComposizione(serie) {
  datiGrafico('graficoComposizione', 'bar', {
    labels: serie.map(function (g) { return g.etichetta; }),
    datasets: [
      { label: 'Materiali', data: serie.map(function (g) { return g.costoMateriali; }), backgroundColor: colore('blu'), stack: 's' },
      { label: 'Manodopera', data: serie.map(function (g) { return g.manoOpera; }), backgroundColor: colore('teal'), stack: 's' }
    ]
  }, Object.assign(opzioniBase(), { scales: { x: asseX({ stacked: true }), y: asseY({ stacked: true }) } }));
}

function aggiornaTutto(periodo) {
  periodoCorrente = periodo;
  document.querySelectorAll('#selettorePeriodo .pill').forEach(function (b) {
    b.classList.toggle('attiva', b.dataset.periodo === periodo);
  });

  var serie = aggrega(periodo);
  var mappa = {};
  serie.forEach(function (g) { mappa[g.chiave] = g; });
  var chiaveOra = chiavePeriodo(new Date().toISOString(), periodo);
  var corrente = mappa[chiaveOra] || { n: 0, valore: 0, margineMedio: 0, prezzoMedio: 0 };
  var precedente = mappa[periodoAdiacente(chiaveOra, periodo, -1)] || null;

  var capitaleMagazzino = DATI.magazzino.reduce(function (s, m) { return s + m.giacenza * m.prezzo_fornitore_medio; }, 0);
  var materialiSottoScorta = DATI.magazzino.filter(function (m) { return m.sotto_scorta; }).length;

  disegnaGrigliaKpi(corrente, precedente, capitaleMagazzino, materialiSottoScorta);
  disegnaSintesi(corrente, precedente);
  disegnaAndamento(serie);
  disegnaMargini(serie);
  disegnaComposizione(serie);
}

// --- Magazzino: grafici e tabella (storico completo, non per periodo) ----

function statoMateriale(m) {
  if (!m.dati_sufficienti) return { classe: '', testo: 'Dati insufficienti' };
  if (m.sotto_scorta) return { classe: 'basso', testo: 'Sotto scorta' };
  if (m.giorni_autonomia_stimati !== null && m.tempo_medio_restock_giorni !== null && m.giorni_autonomia_stimati < m.tempo_medio_restock_giorni) {
    return { classe: 'medio', testo: 'Rischio rottura' };
  }
  if (m.giorni_autonomia_stimati !== null && m.giorni_autonomia_stimati < 14) return { classe: 'medio', testo: 'In esaurimento' };
  return { classe: 'ok', testo: 'OK' };
}

function disegnaGraficoGiacenze() {
  var mag = DATI.magazzino;
  datiGrafico('graficoGiacenze', 'bar', {
    labels: mag.map(function (m) { return m.nome; }),
    datasets: [
      { label: 'Giacenza', data: mag.map(function (m) { return m.giacenza; }), backgroundColor: colore('blu') },
      { label: 'Scorta minima', data: mag.map(function (m) { return m.scorta_minima; }), backgroundColor: colore('rosso') },
      { label: 'Scorta massima', data: mag.map(function (m) { return m.scorta_massima; }), backgroundColor: colore('bordo') }
    ]
  }, Object.assign(opzioniBase(), { indexAxis: 'y', scales: { x: asseX(), y: asseY() } }));
}

function disegnaGraficoAutonomia() {
  var mag = DATI.magazzino.filter(function (m) { return m.dati_sufficienti && m.giorni_autonomia_stimati !== null; })
    .sort(function (a, b) { return a.giorni_autonomia_stimati - b.giorni_autonomia_stimati; });
  var colori = mag.map(function (m) {
    if (m.giorni_autonomia_stimati < 14) return colore('rosso');
    if (m.giorni_autonomia_stimati < 30) return colore('accento');
    return colore('verde');
  });
  datiGrafico('graficoAutonomia', 'bar', {
    labels: mag.map(function (m) { return m.nome; }),
    datasets: [{ label: 'Giorni di autonomia stimati', data: mag.map(function (m) { return m.giorni_autonomia_stimati; }), backgroundColor: colori }]
  }, Object.assign(opzioniBase(), { indexAxis: 'y', plugins: Object.assign(opzioniBase().plugins, { legend: { display: false } }), scales: { x: asseX({ title: { display: true, text: 'Giorni', color: colore('muto') } }), y: asseY() } }));
}

function disegnaTabellaMagazzino() {
  document.getElementById('corpoTabellaMagazzino').innerHTML = DATI.magazzino.map(function (m) {
    var stato = statoMateriale(m);
    var badge = stato.classe ? '<span class="tag ' + stato.classe + '">' + stato.testo + '</span>' : '<span class="testo-muto">' + stato.testo + '</span>';
    return '<tr>' +
      '<td>' + esc(m.nome) + '</td>' +
      '<td class="num">' + formattaNumero(m.giacenza, 1) + '</td>' +
      '<td class="num">' + formattaNumero(m.scorta_minima, 0) + ' / ' + formattaNumero(m.scorta_massima, 0) + '</td>' +
      '<td class="num">' + formattaNumero(m.consumo_ultimi_30gg, 1) + '</td>' +
      '<td class="num">' + (m.dati_sufficienti ? formattaNumero(m.velocita_consumo_giorno, 2) : '—') + '</td>' +
      '<td class="num">' + (m.giorni_autonomia_stimati !== null ? formattaNumero(m.giorni_autonomia_stimati, 0) + ' gg' : '—') + '</td>' +
      '<td class="num">' + (m.tempo_medio_restock_giorni !== null ? formattaNumero(m.tempo_medio_restock_giorni, 0) + ' gg' : '—') + '</td>' +
      '<td>' + formattaData(m.ultimo_restock) + '</td>' +
      '<td>' + badge + '</td>' +
      '</tr>';
  }).join('');
}

function disegnaConsigliMagazzino() {
  var puntiForza = [], puntiAttenzione = [], suggerimenti = [];
  var affidabili = DATI.magazzino.filter(function (m) { return m.dati_sufficienti; });

  var okCount = affidabili.filter(function (m) { return statoMateriale(m).testo === 'OK'; }).length;
  if (okCount > 0) puntiForza.push(okCount + ' material' + (okCount === 1 ? 'e ha' : 'i hanno') + ' scorte sane e nessun rischio di rottura nel breve periodo.');

  var buonRicambio = affidabili.filter(function (m) { return m.tempo_medio_restock_giorni !== null && m.giorni_autonomia_stimati !== null && m.giorni_autonomia_stimati > m.tempo_medio_restock_giorni * 1.5; });
  if (buonRicambio.length > 0) puntiForza.push('Per ' + buonRicambio.length + ' material' + (buonRicambio.length === 1 ? 'e' : 'i') + ' l\'autonomia residua è ampiamente superiore ai tempi di restock abituali.');

  affidabili.forEach(function (m) {
    if (m.sotto_scorta) {
      puntiAttenzione.push(esc(m.nome) + ' è sotto scorta minima (giacenza ' + formattaNumero(m.giacenza, 1) + ' contro un minimo di ' + formattaNumero(m.scorta_minima, 0) + ').');
    } else if (m.giorni_autonomia_stimati !== null && m.giorni_autonomia_stimati < 14) {
      puntiAttenzione.push(esc(m.nome) + ' si esaurirà fra circa ' + formattaNumero(m.giorni_autonomia_stimati, 0) + ' giorni al ritmo di consumo attuale.');
    }
    if (m.trend_consumo_pct !== null && m.trend_consumo_pct > 40) {
      suggerimenti.push('Il consumo di ' + esc(m.nome) + ' è aumentato del ' + formattaNumero(m.trend_consumo_pct, 0) + '% nell\'ultimo mese: valuta di alzare la scorta minima.');
    }
    if (m.tempo_medio_restock_giorni !== null && m.giorni_autonomia_stimati !== null && m.giorni_autonomia_stimati < m.tempo_medio_restock_giorni) {
      suggerimenti.push('Per ' + esc(m.nome) + ' il tempo medio di restock (' + formattaNumero(m.tempo_medio_restock_giorni, 0) + ' gg) supera l\'autonomia stimata (' + formattaNumero(m.giorni_autonomia_stimati, 0) + ' gg): rischio di rimanere senza materiale prima che arrivi il riordino, valuta di anticipare l\'ordine o cercare un fornitore più rapido.');
    }
    if (m.capacita_magazzino > 0 && m.giacenza >= m.capacita_magazzino * 0.9) {
      suggerimenti.push(esc(m.nome) + ' è vicino alla capacità massima di magazzino: capitale immobilizzato, valuta di rallentare i prossimi ordini.');
    }
  });

  DATI.confronto_fornitori.forEach(function (c) {
    if (c.risparmio_pct > 15) {
      suggerimenti.push('Per ' + esc(c.nome) + ' un fornitore alternativo costa il ' + formattaNumero(c.risparmio_pct, 0) + '% in meno rispetto al più caro: valuta di spostare gli ordini.');
    }
  });

  var senzaDati = DATI.magazzino.filter(function (m) { return !m.dati_sufficienti; });
  if (senzaDati.length > 0) {
    suggerimenti.push(senzaDati.length + ' material' + (senzaDati.length === 1 ? 'e non ha' : 'i non hanno') + ' ancora abbastanza movimenti registrati (' + esc(senzaDati.map(function (m) { return m.nome; }).join(', ')) + ') per una stima affidabile: continua a registrare carichi/scarichi.');
  }
  if (puntiAttenzione.length === 0) puntiAttenzione.push('Nessun materiale mostra segnali critici al momento.');
  if (suggerimenti.length === 0) suggerimenti.push('Nessun suggerimento operativo urgente in questo momento.');

  document.getElementById('blocchiConsigliMagazzino').innerHTML =
    bloccoSintesi('Punti di forza', 'forza', puntiForza) +
    bloccoSintesi('Punti di attenzione', 'attenzione', puntiAttenzione) +
    bloccoSintesi('Consigli operativi', 'cambi', suggerimenti);
}

// --- Grafici e tabelle storiche (non per periodo) -------------------------

function disegnaManodopera() {
  datiGrafico('graficoManodopera', 'doughnut', {
    labels: DATI.manodopera.map(function (f) { return f.fase; }),
    datasets: [{ data: DATI.manodopera.map(function (f) { return f.minuti; }), backgroundColor: [colore('blu'), colore('teal'), colore('verde'), colore('accento'), colore('rosso')] }]
  }, opzioniBase());
}

function disegnaMaterialiUsati() {
  datiGrafico('graficoMaterialiUsati', 'bar', {
    labels: DATI.materiali_piu_usati.map(function (m) { return m.nome; }),
    datasets: [{ label: 'Consumo storico', data: DATI.materiali_piu_usati.map(function (m) { return m.consumo; }), backgroundColor: colore('teal') }]
  }, Object.assign(opzioniBase(), { indexAxis: 'y', plugins: Object.assign(opzioniBase().plugins, { legend: { display: false } }), scales: { x: asseX(), y: asseY() } }));
}

function disegnaClienti() {
  datiGrafico('graficoClienti', 'bar', {
    labels: DATI.clienti.map(function (c) { return c.cliente; }),
    datasets: [{ label: 'Valore preventivi', data: DATI.clienti.map(function (c) { return c.valore; }), backgroundColor: colore('blu') }]
  }, Object.assign(opzioniBase(), { indexAxis: 'y', plugins: Object.assign(opzioniBase().plugins, { legend: { display: false } }), scales: { x: asseX(), y: asseY() } }));
}

function disegnaTabellaRicarico() {
  document.getElementById('corpoTabellaRicarico').innerHTML = DATI.ricarico.map(function (r) {
    return '<tr><td>' + esc(r.nome) + '</td><td class="num">' + formattaEuro(r.prezzo_vendita) + '</td><td class="num">' + formattaEuro(r.prezzo_fornitore) + '</td><td class="num">' + formattaNumero(r.ricarico_pct, 0) + '%</td></tr>';
  }).join('');
}

function disegnaTabellaFornitori() {
  if (DATI.confronto_fornitori.length === 0) return;
  document.getElementById('sezioneFornitori').style.display = '';
  var righe = [];
  DATI.confronto_fornitori.forEach(function (c) {
    c.fornitori.forEach(function (f, i) {
      righe.push('<tr><td>' + (i === 0 ? esc(c.nome) : '') + '</td><td>' + esc(f.fornitore) + '</td><td class="num">' + formattaEuro(f.prezzo) + '</td><td class="num">' + formattaNumero(f.giacenza, 1) + '</td><td class="num">' + (i === 0 ? formattaNumero(c.risparmio_pct, 0) + '%' : '') + '</td></tr>');
    });
  });
  document.getElementById('corpoTabellaFornitori').innerHTML = righe.join('');
}

function disegnaTabellaMovimenti() {
  document.getElementById('corpoTabellaMovimenti').innerHTML = DATI.movimenti.map(function (m) {
    return '<tr><td>' + formattaData(m.data) + '</td><td>' + esc(m.materiale) + '</td><td><span class="tag ' + m.tipo + '">' + esc(m.tipo) + '</span></td><td class="num">' + formattaNumero(m.quantita, 1) + '</td><td>' + esc(m.fornitore) + '</td><td class="testo-muto">' + esc(m.note) + '</td></tr>';
  }).join('');
}

function disegnaNotiziario() {
  var contenitore = document.getElementById('contenutoNotiziario');
  if (!DATI.notiziario || !DATI.notiziario.notizie || DATI.notiziario.notizie.length === 0) {
    contenitore.innerHTML = '<div class="suggerimento">Il notiziario di settore non è ancora attivo. Esegui <code>python3 notiziario.py</code> per generare <code>notiziario_ultimo.json</code>.</div>';
    return;
  }
  contenitore.innerHTML = DATI.notiziario.notizie.map(function (n) {
    return '<div class="notizia"><div>' + esc(n.titolo) + '</div>' +
      (n.rilevanza ? '<div class="rilevanza">' + esc(n.rilevanza) + '</div>' : '') +
      '<div class="fonte">' + esc(n.fonte || '') + '</div></div>';
  }).join('');
}

function creaGraficiStatici() {
  disegnaGraficoGiacenze();
  disegnaGraficoAutonomia();
  disegnaTabellaMagazzino();
  disegnaConsigliMagazzino();
  disegnaManodopera();
  disegnaMaterialiUsati();
  disegnaClienti();
  disegnaTabellaRicarico();
  disegnaTabellaFornitori();
  disegnaTabellaMovimenti();
  disegnaNotiziario();
}

document.querySelectorAll('#selettorePeriodo .pill').forEach(function (b) {
  b.addEventListener('click', function () { aggiornaTutto(b.dataset.periodo); });
});

var osservatore = new IntersectionObserver(function (voci) {
  voci.forEach(function (v) { if (v.isIntersecting) v.target.classList.add('visibile'); });
}, { threshold: 0.08 });
document.querySelectorAll('.sezione').forEach(function (s) { osservatore.observe(s); });

aggiornaTutto('mese');
creaGraficiStatici();

if (typeof Chart === 'undefined') {
  document.querySelectorAll('.contenitore-grafico').forEach(function (c) {
    c.innerHTML = '<div class="suggerimento">Chart.js non è disponibile (serve internet al primo avvio per scaricarlo). I numeri e le tabelle restano comunque leggibili.</div>';
  });
}
</script>
</body>
</html>
""")


# ---------------------------------------------------------------------------
# Generazione
# ---------------------------------------------------------------------------


def genera(percorso_db=None, apri_browser=True):
    if percorso_db is None:
        percorso_db = risolvi_percorso_db()
    conn = apri_db_sola_lettura(percorso_db)
    try:
        oggi = datetime.now()
        magazzino = estrai_magazzino(conn, oggi)
        n_materiali = max(len(magazzino), 1)
        n_autonomia = max(len([m for m in magazzino if m["dati_sufficienti"] and m["giorni_autonomia_stimati"] is not None]), 1)
        dati = {
            "preventivi": estrai_preventivi(conn),
            "magazzino": magazzino,
            "confronto_fornitori": estrai_confronto_fornitori(conn),
            "movimenti": estrai_movimenti(conn),
            "manodopera": estrai_manodopera(conn),
            "materiali_piu_usati": estrai_materiali_piu_usati(conn),
            "clienti": estrai_clienti(conn),
            "ricarico": estrai_ricarico(conn),
            "notiziario": carica_notiziario(),
        }
    finally:
        conn.close()

    contenuto_chartjs, usa_cdn = ottieni_chartjs()
    if usa_cdn:
        tag_chartjs = '<script src="' + CHARTJS_URL + '"></script>'
    else:
        tag_chartjs = "<script>\n" + contenuto_chartjs + "\n</script>"

    json_dati = json.dumps(dati, ensure_ascii=False).replace("</", "<\\/")

    html = HTML_TEMPLATE.substitute(
        TAG_CHARTJS=tag_chartjs,
        JSON_DATI=json_dati,
        DATA_GENERAZIONE=oggi.strftime("%d/%m/%Y %H:%M"),
        ALTEZZA_GRAFICO_MAGAZZINO=max(n_materiali * 26, 260),
        ALTEZZA_GRAFICO_AUTONOMIA=max(n_autonomia * 26, 260),
    )

    cartella_report = os.path.join(CARTELLA_PROGETTO, "report")
    os.makedirs(cartella_report, exist_ok=True)
    percorso_report = os.path.join(cartella_report, "report_" + oggi.strftime("%Y%m%d_%H%M") + ".html")
    percorso_ultimo = os.path.join(cartella_report, "ultimo.html")

    with open(percorso_report, "w", encoding="utf-8") as f:
        f.write(html)
    with open(percorso_ultimo, "w", encoding="utf-8") as f:
        f.write(html)

    if apri_browser:
        webbrowser.open("file://" + os.path.abspath(percorso_ultimo))

    return percorso_ultimo


def main():
    apri_browser = "--no-open" not in sys.argv
    try:
        percorso = genera(apri_browser=apri_browser)
        print("Report generato:", percorso)
    except ConfigDbError as e:
        print("Errore:", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
