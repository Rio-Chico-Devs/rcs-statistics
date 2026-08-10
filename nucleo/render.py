"""Assemblaggio del file HTML finale.

Lo scheletro HTML sta qui; foglio di stile e logica stanno in
``assets_web/stile.css`` e ``assets_web/app.js`` e vengono incorporati al
momento della generazione. Il risultato resta un file unico e autosufficiente,
ma i sorgenti si scrivono e si testano come CSS e JavaScript veri.

Nota: CSS e JS entrano come *valori* di ``Template.substitute``, non come
parte del template, quindi un ``$`` nel loro contenuto non viene interpretato
e non serve raddoppiarlo.
"""

import json
import os
import urllib.request
from string import Template

from .utils import log, percorso_dati, percorso_risorsa

CHARTJS_VERSION = "4.4.9"
CHARTJS_URL = (
    "https://cdn.jsdelivr.net/npm/chart.js@" + CHARTJS_VERSION + "/dist/chart.umd.min.js"
)
DIMENSIONE_MINIMA_VALIDA = 100_000


def ottieni_chartjs():
    """``(contenuto, usa_cdn)``: cache locale, poi download, poi ripiego CDN.

    Se il download fallisce il report punta al CDN: servira' internet per
    vedere i grafici, ma numeri e tabelle restano leggibili comunque.
    """
    cache = percorso_dati("assets", "chart.umd.min.js")
    if os.path.isfile(cache) and os.path.getsize(cache) > DIMENSIONE_MINIMA_VALIDA:
        try:
            with open(cache, "r", encoding="utf-8") as f:
                return f.read(), False
        except OSError as e:
            log("Cache Chart.js illeggibile: " + str(e))
    try:
        with urllib.request.urlopen(CHARTJS_URL, timeout=10) as resp:
            contenuto = resp.read().decode("utf-8")
        if len(contenuto) > DIMENSIONE_MINIMA_VALIDA:
            os.makedirs(os.path.dirname(cache), exist_ok=True)
            with open(cache, "w", encoding="utf-8") as f:
                f.write(contenuto)
            return contenuto, False
    except Exception as e:  # noqa: BLE001 - qualunque problema di rete/disco
        log("Chart.js non scaricabile, si usa il CDN: " + str(e))
    return None, True


def _leggi_risorsa(nome):
    with open(percorso_risorsa("assets_web", nome), "r", encoding="utf-8") as f:
        return f.read()


SCHELETRO = Template(
    """<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>RCS &middot; Cruscotto Aziendale</title>
<style>
${STILE}
</style>
</head>
<body>
<div class="contenitore">

  <header class="testata">
    <div class="logo">RCS <span>&middot;</span> Cruscotto Aziendale</div>
    <div class="meta-testata">
      <span class="data-generazione">Generato il ${DATA_LEGGIBILE}</span>
      <div class="selettore-periodo" id="selettorePeriodo" role="group" aria-label="Periodo">
        <button class="pill" data-periodo="mese" aria-pressed="false">Mese</button>
        <button class="pill" data-periodo="trimestre" aria-pressed="false">Trimestre</button>
        <button class="pill" data-periodo="anno" aria-pressed="false">Anno</button>
      </div>
    </div>
  </header>

  <div class="griglia-kpi" id="grigliaKpi"></div>

  <div class="sezione">
    <h2>In sintesi <span class="nota-periodo" id="notaSintesi"></span></h2>
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
    <h2>Magazzino <span class="nota-periodo">giacenze, consumo e riordino &mdash; storico completo</span></h2>
    <div class="griglia-2" style="margin-bottom:20px;">
      <div class="contenitore-grafico" style="height:${ALTEZZA_GIACENZE}px;"><canvas id="graficoGiacenze"></canvas></div>
      <div class="contenitore-grafico" style="height:${ALTEZZA_AUTONOMIA}px;"><canvas id="graficoAutonomia"></canvas></div>
    </div>
    <div class="tabella-scroll" style="margin-bottom:18px;">
      <table>
        <thead>
          <tr>
            <th>Materiale</th><th class="num">Giacenza</th><th class="num">Scorta min/max</th>
            <th class="num">Consumo 30gg</th><th class="num">Velocit&agrave; /giorno</th>
            <th class="num">Autonomia</th><th class="num">Riordino ogni</th>
            <th>Ultimo carico</th><th>Stato</th>
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

  <div class="sezione">
    <h2>Storico prezzi fornitore <span class="nota-periodo">rilevato dal Cruscotto</span></h2>
    <div class="tabella-scroll" style="margin-bottom:14px;">
      <table>
        <thead><tr><th>Materiale</th><th class="num">Primo prezzo rilevato</th><th class="num">Prezzo attuale</th><th class="num">Variazione</th></tr></thead>
        <tbody id="corpoTabellaStoricoPrezzi"></tbody>
      </table>
    </div>
    <div id="notaStoricoPrezzi" class="suggerimento"></div>
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

  <div class="sezione">
    <h2>Notiziario di settore</h2>
    <div id="contenutoNotiziario"></div>
  </div>

  <footer class="piede">Cruscotto Aziendale RCS &middot; report generato in locale, nessun dato lascia il computer</footer>
</div>

${TAG_CHARTJS}
<script>
var DATI_REPORT = ${JSON_DATI};
</script>
<script>
${APP_JS}
</script>
</body>
</html>
"""
)


def componi_html(dati, oggi):
    """Restituisce l'HTML completo del report."""
    contenuto_chartjs, usa_cdn = ottieni_chartjs()
    if usa_cdn:
        tag_chartjs = '<script src="' + CHARTJS_URL + '"></script>'
    else:
        tag_chartjs = "<script>\n" + contenuto_chartjs + "\n</script>"

    # L'escape di "</" evita che un nome cliente o materiale contenente
    # "</script>" chiuda il tag in anticipo.
    json_dati = json.dumps(dati, ensure_ascii=False).replace("</", "<\\/")

    n_materiali = max(len(dati["magazzino"]), 1)
    n_autonomia = max(
        len([m for m in dati["magazzino"] if m.get("giorni_autonomia_prudente") is not None]), 1
    )

    return SCHELETRO.substitute(
        STILE=_leggi_risorsa("stile.css"),
        APP_JS=_leggi_risorsa("app.js"),
        TAG_CHARTJS=tag_chartjs,
        JSON_DATI=json_dati,
        DATA_LEGGIBILE=oggi.strftime("%d/%m/%Y %H:%M"),
        ALTEZZA_GIACENZE=max(n_materiali * 26, 260),
        ALTEZZA_AUTONOMIA=max(n_autonomia * 26, 260),
    )
