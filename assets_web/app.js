/* Cruscotto Aziendale RCS - logica del report.

   File JavaScript vero (non una stringa dentro il Python): si legge con
   l'evidenziazione della sintassi, si controlla con un linter e soprattutto
   si testa (vedi test/test_app.js, che importa le funzioni pure qui sotto).

   Struttura del file:
     1. funzioni pure        - calcoli e testi, nessun tocco al DOM: testabili
     2. formattazione        - numeri, date, testo in italiano
     3. disegno              - grafici e tabelle, toccano il DOM
     4. avvio                - eseguito solo dentro il browser

   Convenzione: niente template literal con backtick, concatenazione con "+",
   per restare coerenti con il resto del progetto. */

/* ============================================================
   1. Funzioni pure (nessun DOM: testate da test/test_app.js)
   ============================================================ */

function chiavePeriodo(dataIso, periodo) {
  var d = new Date(dataIso);
  var anno = d.getFullYear();
  var mese = d.getMonth();
  if (periodo === 'mese') return anno + '-' + String(mese + 1).padStart(2, '0');
  if (periodo === 'trimestre') return anno + '-T' + (Math.floor(mese / 3) + 1);
  return '' + anno;
}

function etichettaPeriodo(chiave, periodo) {
  if (periodo === 'mese') {
    var mesi = ['Gen', 'Feb', 'Mar', 'Apr', 'Mag', 'Giu', 'Lug', 'Ago', 'Set', 'Ott', 'Nov', 'Dic'];
    var parti = chiave.split('-');
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
  var partiMese = chiave.split('-');
  var totaleMesi = parseInt(partiMese[0], 10) * 12 + (parseInt(partiMese[1], 10) - 1) + offset;
  return Math.floor(totaleMesi / 12) + '-' + String((totaleMesi % 12) + 1).padStart(2, '0');
}

/* Raggruppa i preventivi per periodo.
   Il margine e' ponderato sul fatturato (ricavi totali - costi totali), non la
   media delle percentuali dei singoli preventivi: un preventivo con prezzo
   quasi zero altrimenti farebbe esplodere la media pur pesando pochissimo. */
function aggrega(preventivi, periodo) {
  var gruppi = {};
  preventivi.forEach(function (p) {
    var chiave = chiavePeriodo(p.data, periodo);
    if (!gruppi[chiave]) {
      gruppi[chiave] = { chiave: chiave, n: 0, valore: 0, costoTotale: 0, costoMateriali: 0, manoOpera: 0 };
    }
    var g = gruppi[chiave];
    g.n += 1;
    g.valore += p.prezzo;
    g.costoTotale += p.costo_totale;
    g.costoMateriali += p.costo_materiali;
    g.manoOpera += p.mano_opera;
  });
  return Object.keys(gruppi).sort().map(function (k) {
    var g = gruppi[k];
    return {
      chiave: k,
      etichetta: etichettaPeriodo(k, periodo),
      n: g.n,
      valore: g.valore,
      margineMedio: g.valore > 0 ? (g.valore - g.costoTotale) / g.valore * 100 : 0,
      prezzoMedio: g.n > 0 ? g.valore / g.n : 0,
      costoMateriali: g.costoMateriali,
      manoOpera: g.manoOpera
    };
  });
}

/* Testo che dice a quale periodo si riferiscono i numeri in alto.
   Senza, un mese ancora senza preventivi mostra sei zeri e sembra un guasto
   invece di "in questo mese non e' ancora stato fatto nulla". */
function descrizioneConfronto(chiaveCorrente, periodo, haPrecedente) {
  var testo = etichettaPeriodo(chiaveCorrente, periodo);
  if (haPrecedente) {
    testo += ' rispetto a ' + etichettaPeriodo(periodoAdiacente(chiaveCorrente, periodo, -1), periodo);
  } else {
    testo += ' · nessun periodo precedente da confrontare';
  }
  return testo;
}

function calcolaDelta(attuale, precedente) {
  if (precedente === null || precedente === undefined || precedente === 0) return null;
  return ((attuale - precedente) / Math.abs(precedente)) * 100;
}

function plurale(n, singolare, plurali) {
  return n === 1 ? singolare : plurali;
}

/* Elenco dei materiali sotto la scorta minima, ordinati per gravita'
   (quanto manca in percentuale rispetto al minimo). */
function materialiSottoScorta(magazzino) {
  return magazzino
    .filter(function (m) { return m.sotto_scorta; })
    .sort(function (a, b) {
      var ga = a.scorta_minima > 0 ? a.giacenza / a.scorta_minima : 1;
      var gb = b.scorta_minima > 0 ? b.giacenza / b.scorta_minima : 1;
      return ga - gb;
    });
}

/* Le tre colonne di "In sintesi". Pura: restituisce liste di frasi. */
function costruisciSintesi(corrente, precedente, magazzino) {
  var forza = [], attenzione = [], cambi = [];

  if (precedente) {
    var deltaValore = calcolaDelta(corrente.valore, precedente.valore);
    var deltaMargine = corrente.margineMedio - precedente.margineMedio;
    if (deltaValore !== null && deltaValore > 5) {
      forza.push('Il valore preventivato è salito del ' + formattaNumero(deltaValore, 1) + '% rispetto al periodo precedente.');
    }
    if (deltaMargine > 1) {
      forza.push('Il margine medio è migliorato di ' + formattaNumero(deltaMargine, 1) + ' punti percentuali.');
    }
    if (deltaValore !== null && deltaValore < -5) {
      attenzione.push('Il valore preventivato è sceso del ' + formattaNumero(Math.abs(deltaValore), 1) + '% rispetto al periodo precedente.');
    }
    if (deltaMargine < -1) {
      attenzione.push('Il margine medio è peggiorato di ' + formattaNumero(Math.abs(deltaMargine), 1) + ' punti percentuali.');
    }
  } else {
    cambi.push('Ancora nessun periodo precedente da confrontare.');
  }

  var sotto = materialiSottoScorta(magazzino);
  if (sotto.length > 0) {
    var nomi = sotto.slice(0, 3).map(function (m) { return m.nome; }).join(', ');
    attenzione.push(sotto.length + ' ' + plurale(sotto.length, 'materiale', 'materiali') +
      ' sotto la scorta minima: ' + nomi + (sotto.length > 3 ? '…' : '') + '.');
  } else {
    forza.push('Nessun materiale è sotto la scorta minima al momento.');
  }

  var senzaDati = magazzino.filter(function (m) { return !m.dati_sufficienti; });
  if (senzaDati.length > 0) {
    cambi.push(senzaDati.length + ' ' + plurale(senzaDati.length, 'materiale non ha', 'materiali non hanno') +
      ' ancora abbastanza storico per una stima di consumo affidabile.');
  }
  return { forza: forza, attenzione: attenzione, cambi: cambi };
}

/* I consigli operativi sul magazzino. Pura: e' la parte "intelligente" del
   report, quindi e' quella che piu' merita di essere testata. */
function costruisciConsigliMagazzino(magazzino, confrontoFornitori, dataGenerazione, imp) {
  imp = imp || {};
  var maxPerTipo = imp.max_consigli_per_tipo || 3;
  var sogliaConsumo = imp.soglia_aumento_consumo_pct || 40;
  var sogliaPrezzo = imp.soglia_aumento_prezzo_pct || 8;
  var sogliaRisparmio = imp.soglia_risparmio_fornitore_pct || 15;
  var sogliaRiempimento = imp.soglia_riempimento_magazzino || 0.9;

  var forza = [], attenzione = [], consigli = [];
  var affidabili = magazzino.filter(function (m) { return m.dati_sufficienti; });

  // --- Punti di forza ---
  var sereni = affidabili.filter(function (m) {
    return !m.sotto_scorta && !m.da_riordinare;
  });
  if (sereni.length > 0) {
    forza.push(sereni.length + ' ' + plurale(sereni.length, 'materiale ha', 'materiali hanno') +
      ' scorte sufficienti a coprire i tempi di riordino abituali.');
  }
  var prezziScesi = magazzino.filter(function (m) {
    return m.n_rilevazioni_prezzo >= 2 && m.variazione_prezzo_pct < -2;
  });
  if (prezziScesi.length > 0) {
    forza.push('Il prezzo fornitore è sceso su ' + prezziScesi.length + ' ' +
      plurale(prezziScesi.length, 'materiale', 'materiali') + ' da quando li monitoriamo.');
  }

  // --- Punti di attenzione ---
  materialiSottoScorta(affidabili).slice(0, maxPerTipo).forEach(function (m) {
    attenzione.push(m.nome + ' è sotto scorta minima: ' + formattaNumero(m.giacenza, 1) +
      ' contro un minimo di ' + formattaNumero(m.scorta_minima, 0) + '.');
  });
  affidabili
    .filter(function (m) {
      return !m.sotto_scorta && m.giorni_autonomia_prudente !== null &&
        m.giorni_autonomia_prudente < (imp.giorni_allarme_esaurimento || 14);
    })
    .sort(function (a, b) { return a.giorni_autonomia_prudente - b.giorni_autonomia_prudente; })
    .slice(0, maxPerTipo)
    .forEach(function (m) {
      attenzione.push(m.nome + ' si esaurirà fra circa ' +
        formattaNumero(m.giorni_autonomia_prudente, 0) + ' giorni al ritmo di consumo attuale.');
    });

  // --- Consigli: riordini, il consiglio piu' azionabile di tutti ---
  affidabili
    .filter(function (m) { return m.da_riordinare && m.quantita_da_ordinare > 0; })
    .sort(function (a, b) { return (a.giorni_entro_cui_ordinare || 0) - (b.giorni_entro_cui_ordinare || 0); })
    .slice(0, maxPerTipo)
    .forEach(function (m) {
      var quando = 'appena possibile';
      if (m.giorni_entro_cui_ordinare !== null && m.giorni_entro_cui_ordinare > 0) {
        quando = 'entro il ' + formattaData(aggiungiGiorni(dataGenerazione, m.giorni_entro_cui_ordinare));
      }
      consigli.push('Ordina circa ' + formattaNumero(m.quantita_da_ordinare, 0) + ' unità di ' +
        m.nome + ' ' + quando + ': con ' + formattaNumero(m.lead_time_giorni, 0) +
        ' giorni di attesa abituali, la scorta non basta a coprire la consegna.');
    });

  // --- Consigli: consumo in forte crescita ---
  affidabili
    .filter(function (m) {
      // Una percentuale su una base quasi nulla e' rumore: qualunque consumo
      // recente sembrerebbe un'impennata del +500%. Serve una base minima.
      var baseMinima = Math.max(m.scorta_minima * 0.15, 3);
      return m.trend_consumo_pct !== null && m.trend_consumo_pct > sogliaConsumo &&
        m.consumo_30_60gg_fa >= baseMinima;
    })
    .sort(function (a, b) { return b.consumo_ultimi_30gg - a.consumo_ultimi_30gg; })
    .slice(0, maxPerTipo)
    .forEach(function (m) {
      consigli.push('Il consumo di ' + m.nome + ' è cresciuto molto nell\'ultimo mese (' +
        formattaNumero(m.consumo_ultimi_30gg, 0) + ' unità contro ' +
        formattaNumero(m.consumo_30_60gg_fa, 0) + ' nel mese precedente): valuta di alzare la scorta minima.');
    });

  // --- Consigli: aumenti di prezzo osservati ---
  magazzino
    .filter(function (m) { return m.n_rilevazioni_prezzo >= 2 && m.variazione_prezzo_pct > sogliaPrezzo; })
    .sort(function (a, b) { return b.variazione_prezzo_pct - a.variazione_prezzo_pct; })
    .slice(0, maxPerTipo)
    .forEach(function (m) {
      consigli.push('Il prezzo fornitore di ' + m.nome + ' è salito del ' +
        formattaNumero(m.variazione_prezzo_pct, 0) + '% dal ' +
        formattaData(m.prima_rilevazione_prezzo) + ': valuta di rivedere il prezzo di vendita.');
    });

  // --- Consigli: fornitori alternativi ---
  (confrontoFornitori || [])
    .filter(function (c) { return c.risparmio_pct > sogliaRisparmio; })
    .slice(0, maxPerTipo)
    .forEach(function (c) {
      consigli.push('Per ' + c.nome + ' un fornitore alternativo costa il ' +
        formattaNumero(c.risparmio_pct, 0) + '% in meno del più caro: valuta di spostare gli ordini.');
    });

  // --- Consigli: capitale fermo ---
  affidabili
    .filter(function (m) {
      return m.capacita_magazzino > 0 && m.giacenza >= m.capacita_magazzino * sogliaRiempimento;
    })
    .slice(0, maxPerTipo)
    .forEach(function (m) {
      consigli.push(m.nome + ' è quasi al limite di capienza (' + formattaEuro(m.valore_giacenza) +
        ' di capitale fermo): valuta di rallentare i prossimi ordini.');
    });

  var senzaDati = magazzino.filter(function (m) { return !m.dati_sufficienti; });
  if (senzaDati.length > 0) {
    consigli.push(senzaDati.length + ' ' + plurale(senzaDati.length, 'materiale non ha', 'materiali non hanno') +
      ' abbastanza movimenti registrati (' + senzaDati.map(function (m) { return m.nome; }).join(', ') +
      '): continua a registrare carichi e scarichi per ottenere una stima.');
  }
  if (attenzione.length === 0) attenzione.push('Nessun materiale mostra segnali critici al momento.');
  if (consigli.length === 0) consigli.push('Nessun intervento urgente sul magazzino in questo momento.');

  return { forza: forza, attenzione: attenzione, consigli: consigli };
}

/* ============================================================
   2. Formattazione
   ============================================================ */

function esc(s) {
  return String(s === undefined || s === null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function formattaEuro(v) {
  return '€ ' + (v || 0).toLocaleString('it-IT', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formattaNumero(v, decimali) {
  var d = decimali || 0;
  return (v || 0).toLocaleString('it-IT', { minimumFractionDigits: d, maximumFractionDigits: d });
}

function formattaData(iso) {
  if (!iso) return '—';
  var d = new Date(iso);
  if (isNaN(d.getTime())) return '—';
  return String(d.getDate()).padStart(2, '0') + '/' +
    String(d.getMonth() + 1).padStart(2, '0') + '/' + d.getFullYear();
}

function aggiungiGiorni(iso, giorni) {
  var d = new Date(iso);
  d.setDate(d.getDate() + giorni);
  return d.toISOString();
}

function formattaGiorni(v) {
  return v === null || v === undefined ? '—' : formattaNumero(v, 0) + ' gg';
}

/* ============================================================
   3. Disegno (DOM + Chart.js)
   ============================================================ */

var DATI = typeof DATI_REPORT !== 'undefined' ? DATI_REPORT : { magazzino: [], preventivi: [] };
var graficiAttivi = {};
var periodoCorrente = 'mese';

function colore(nome) {
  return getComputedStyle(document.documentElement).getPropertyValue('--' + nome).trim();
}

function animaNumero(elemento, valoreFinale, formattatore) {
  var durata = 750;
  var inizio = null;
  // Chi ha chiesto meno animazioni a livello di sistema vede subito il valore.
  if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    elemento.textContent = formattatore(valoreFinale);
    return;
  }
  function passo(timestamp) {
    if (inizio === null) inizio = timestamp;
    var f = Math.min((timestamp - inizio) / durata, 1);
    var eased = 1 - Math.pow(1 - f, 3);
    elemento.textContent = formattatore(valoreFinale * eased);
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

function asse(extra) {
  var base = { grid: { color: colore('bordo') }, ticks: { color: colore('muto') } };
  if (extra) for (var k in extra) base[k] = extra[k];
  return base;
}

function senzaLegenda(opzioni) {
  opzioni.plugins = Object.assign({}, opzioni.plugins, { legend: { display: false } });
  return opzioni;
}

function datiGrafico(id, tipo, dati, opzioni) {
  var canvas = document.getElementById(id);
  if (!canvas || typeof Chart === 'undefined') return null;
  if (graficiAttivi[id]) graficiAttivi[id].destroy();
  graficiAttivi[id] = new Chart(canvas.getContext('2d'), { type: tipo, data: dati, options: opzioni });
  return graficiAttivi[id];
}

function kpiCard(id, etichetta, deltaPct) {
  var classe = 'neutro', freccia = '—', testo = 'nessun confronto disponibile';
  if (deltaPct !== null && deltaPct !== undefined && isFinite(deltaPct)) {
    classe = deltaPct > 0.5 ? 'su' : (deltaPct < -0.5 ? 'giu' : 'neutro');
    freccia = deltaPct > 0.5 ? '▲' : (deltaPct < -0.5 ? '▼' : '—');
    testo = (deltaPct > 0 ? '+' : '') + formattaNumero(deltaPct, 1) + '% vs periodo prec.';
  }
  return '<div class="kpi">' +
    '<div class="etichetta">' + esc(etichetta) + '</div>' +
    '<div class="valore" id="valoreKpi' + id + '">0</div>' +
    '<div class="delta ' + classe + '">' + freccia + ' <span>' + testo + '</span></div>' +
    '</div>';
}

function disegnaGrigliaKpi(corrente, precedente, capitale, nSottoScorta) {
  function delta(campo) {
    return calcolaDelta(corrente[campo], precedente ? precedente[campo] : null);
  }
  document.getElementById('grigliaKpi').innerHTML =
    kpiCard('NPrev', 'N. preventivi', delta('n')) +
    kpiCard('Valore', 'Valore preventivato', delta('valore')) +
    kpiCard('Margine', 'Margine medio', delta('margineMedio')) +
    kpiCard('Prezzo', 'Prezzo medio', delta('prezzoMedio')) +
    kpiCard('Capitale', 'Capitale a magazzino', null) +
    kpiCard('SottoScorta', 'Materiali sotto scorta', null);

  var intero = function (v) { return formattaNumero(v, 0); };
  animaNumero(document.getElementById('valoreKpiNPrev'), corrente.n, intero);
  animaNumero(document.getElementById('valoreKpiValore'), corrente.valore, formattaEuro);
  animaNumero(document.getElementById('valoreKpiMargine'), corrente.margineMedio, function (v) { return formattaNumero(v, 1) + '%'; });
  animaNumero(document.getElementById('valoreKpiPrezzo'), corrente.prezzoMedio, formattaEuro);
  animaNumero(document.getElementById('valoreKpiCapitale'), capitale, formattaEuro);
  animaNumero(document.getElementById('valoreKpiSottoScorta'), nSottoScorta, intero);

  document.querySelectorAll('.kpi').forEach(function (el) { el.classList.add('visibile'); });
}

function bloccoSintesi(titolo, classe, righe) {
  var lista = righe.length
    ? '<ul>' + righe.map(function (r) { return '<li>' + esc(r) + '</li>'; }).join('') + '</ul>'
    : '<div class="vuoto">Nessuna nota per questo periodo.</div>';
  return '<div class="blocco-sintesi ' + classe + '"><h3>' + esc(titolo) + '</h3>' + lista + '</div>';
}

function disegnaSintesi(corrente, precedente) {
  var s = costruisciSintesi(corrente, precedente, DATI.magazzino);
  document.getElementById('blocchiSintesi').innerHTML =
    bloccoSintesi('Punti di forza', 'forza', s.forza) +
    bloccoSintesi('Punti di attenzione', 'attenzione', s.attenzione) +
    bloccoSintesi('Cambiamenti', 'cambi', s.cambi);
}

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
      x: asse(),
      y: asse({ position: 'left', title: { display: true, text: 'N. preventivi', color: colore('muto') } }),
      y1: asse({ position: 'right', grid: { display: false }, title: { display: true, text: 'Valore (€)', color: colore('muto') } })
    }
  }));
}

function disegnaMargini(serie) {
  datiGrafico('graficoMargini', 'line', {
    labels: serie.map(function (g) { return g.etichetta; }),
    datasets: [{ label: 'Margine medio %', data: serie.map(function (g) { return g.margineMedio; }), borderColor: colore('verde'), backgroundColor: colore('verde'), tension: .3, fill: false }]
  }, Object.assign(opzioniBase(), { scales: { x: asse(), y: asse() } }));
}

function disegnaComposizione(serie) {
  datiGrafico('graficoComposizione', 'bar', {
    labels: serie.map(function (g) { return g.etichetta; }),
    datasets: [
      { label: 'Materiali', data: serie.map(function (g) { return g.costoMateriali; }), backgroundColor: colore('blu'), stack: 's' },
      { label: 'Manodopera', data: serie.map(function (g) { return g.manoOpera; }), backgroundColor: colore('teal'), stack: 's' }
    ]
  }, Object.assign(opzioniBase(), { scales: { x: asse({ stacked: true }), y: asse({ stacked: true }) } }));
}

function aggiornaTutto(periodo) {
  periodoCorrente = periodo;
  document.querySelectorAll('#selettorePeriodo .pill').forEach(function (b) {
    var attiva = b.dataset.periodo === periodo;
    b.classList.toggle('attiva', attiva);
    b.setAttribute('aria-pressed', attiva ? 'true' : 'false');
  });

  var serie = aggrega(DATI.preventivi, periodo);
  var mappa = {};
  serie.forEach(function (g) { mappa[g.chiave] = g; });

  var chiaveOra = chiavePeriodo(DATI.data_generazione, periodo);
  var corrente = mappa[chiaveOra] || { n: 0, valore: 0, margineMedio: 0, prezzoMedio: 0 };
  var precedente = mappa[periodoAdiacente(chiaveOra, periodo, -1)] || null;

  document.getElementById('notaSintesi').textContent =
    descrizioneConfronto(chiaveOra, periodo, precedente !== null);

  // Capitale esatto: ogni lotto vale il prezzo del suo fornitore.
  var capitale = DATI.magazzino.reduce(function (s, m) { return s + m.valore_giacenza; }, 0);

  disegnaGrigliaKpi(corrente, precedente, capitale, materialiSottoScorta(DATI.magazzino).length);
  disegnaSintesi(corrente, precedente);
  disegnaAndamento(serie);
  disegnaMargini(serie);
  disegnaComposizione(serie);
}

function disegnaGraficoGiacenze() {
  var mag = DATI.magazzino;
  datiGrafico('graficoGiacenze', 'bar', {
    labels: mag.map(function (m) { return m.nome; }),
    datasets: [
      { label: 'Giacenza', data: mag.map(function (m) { return m.giacenza; }), backgroundColor: colore('blu') },
      { label: 'Scorta minima', data: mag.map(function (m) { return m.scorta_minima; }), backgroundColor: colore('rosso') },
      { label: 'Punto di riordino', data: mag.map(function (m) { return m.punto_riordino; }), backgroundColor: colore('accento') }
    ]
  }, Object.assign(opzioniBase(), { indexAxis: 'y', scales: { x: asse(), y: asse() } }));
}

function disegnaGraficoAutonomia() {
  var mag = DATI.magazzino
    .filter(function (m) { return m.giorni_autonomia_prudente !== null; })
    .sort(function (a, b) { return a.giorni_autonomia_prudente - b.giorni_autonomia_prudente; });
  var allarme = DATI.impostazioni.giorni_allarme_esaurimento;
  var attenzione = DATI.impostazioni.giorni_attenzione_esaurimento;
  datiGrafico('graficoAutonomia', 'bar', {
    labels: mag.map(function (m) { return m.nome; }),
    datasets: [{
      label: 'Giorni di autonomia',
      data: mag.map(function (m) { return m.giorni_autonomia_prudente; }),
      backgroundColor: mag.map(function (m) {
        if (m.giorni_autonomia_prudente < allarme) return colore('rosso');
        if (m.giorni_autonomia_prudente < attenzione) return colore('accento');
        return colore('verde');
      })
    }]
  }, senzaLegenda(Object.assign(opzioniBase(), {
    indexAxis: 'y',
    scales: { x: asse({ title: { display: true, text: 'Giorni', color: colore('muto') } }), y: asse() }
  })));
}

function dettaglioMateriale(m) {
  // Ogni coppia etichetta/valore sta in un <div>: senza, la griglia CSS
  // tratterebbe dt e dd come celle indipendenti e le scollegherebbe.
  function voce(etichetta, valore) {
    return '<div><dt>' + esc(etichetta) + '</dt><dd>' + valore + '</dd></div>';
  }
  var righe =
    voce('Punto di riordino', m.punto_riordino !== null ? formattaNumero(m.punto_riordino, 1) : '—') +
    voce('Tempo di consegna abituale', formattaGiorni(m.lead_time_giorni)) +
    voce('Consumo mese precedente', formattaNumero(m.consumo_30_60gg_fa, 1)) +
    voce('Capitale fermo', formattaEuro(m.valore_giacenza)) +
    voce('Fornitori', formattaNumero(m.n_fornitori, 0)) +
    voce('Movimenti registrati', formattaNumero(m.n_movimenti, 0));
  if (m.da_riordinare && m.quantita_da_ordinare > 0) {
    righe = voce('Da ordinare', '<strong>' + formattaNumero(m.quantita_da_ordinare, 0) + ' unità</strong>' +
      (m.giorni_entro_cui_ordinare !== null ? ' entro ' + formattaGiorni(m.giorni_entro_cui_ordinare) : '')) + righe;
  }
  return '<tr class="riga-dettaglio"><td colspan="9"><dl>' + righe + '</dl></td></tr>';
}

function disegnaTabellaMagazzino() {
  var html = DATI.magazzino.map(function (m, i) {
    var badge = m.stato_classe
      ? '<span class="tag ' + m.stato_classe + '">' + esc(m.stato_testo) + '</span>'
      : '<span class="testo-muto">' + esc(m.stato_testo) + '</span>';
    return '<tr class="riga-materiale" data-indice="' + i + '" tabindex="0" role="button" ' +
      'aria-expanded="false" title="Clicca per i dettagli di riordino">' +
      '<td>' + esc(m.nome) + '</td>' +
      '<td class="num">' + formattaNumero(m.giacenza, 1) + '</td>' +
      '<td class="num">' + formattaNumero(m.scorta_minima, 0) + ' / ' + formattaNumero(m.scorta_massima, 0) + '</td>' +
      '<td class="num">' + formattaNumero(m.consumo_ultimi_30gg, 1) + '</td>' +
      '<td class="num">' + (m.dati_sufficienti ? formattaNumero(m.velocita_consumo_giorno, 2) : '—') + '</td>' +
      '<td class="num">' + formattaGiorni(m.giorni_autonomia_prudente) + '</td>' +
      '<td class="num">' + formattaGiorni(m.tempo_restock_mediano_giorni) + '</td>' +
      '<td>' + formattaData(m.ultimo_restock) + '</td>' +
      '<td>' + badge + '</td>' +
      '</tr>' + dettaglioMateriale(m);
  }).join('');
  var corpo = document.getElementById('corpoTabellaMagazzino');
  corpo.innerHTML = html;
  corpo.querySelectorAll('.riga-dettaglio').forEach(function (r) { r.style.display = 'none'; });

  function alterna(riga) {
    var dettaglio = riga.nextElementSibling;
    if (!dettaglio || !dettaglio.classList.contains('riga-dettaglio')) return;
    var aperto = dettaglio.style.display !== 'none';
    dettaglio.style.display = aperto ? 'none' : '';
    riga.setAttribute('aria-expanded', aperto ? 'false' : 'true');
  }
  corpo.querySelectorAll('.riga-materiale').forEach(function (riga) {
    riga.addEventListener('click', function () { alterna(riga); });
    riga.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); alterna(riga); }
    });
  });
}

function disegnaConsigliMagazzino() {
  var c = costruisciConsigliMagazzino(
    DATI.magazzino, DATI.confronto_fornitori, DATI.data_generazione, DATI.impostazioni);
  document.getElementById('blocchiConsigliMagazzino').innerHTML =
    bloccoSintesi('Punti di forza', 'forza', c.forza) +
    bloccoSintesi('Punti di attenzione', 'attenzione', c.attenzione) +
    bloccoSintesi('Consigli operativi', 'cambi', c.consigli);
}

function disegnaManodopera() {
  datiGrafico('graficoManodopera', 'doughnut', {
    labels: DATI.manodopera.map(function (f) { return f.fase; }),
    datasets: [{
      data: DATI.manodopera.map(function (f) { return f.minuti; }),
      backgroundColor: [colore('blu'), colore('teal'), colore('verde'), colore('accento'), colore('rosso')]
    }]
  }, opzioniBase());
}

function disegnaMaterialiUsati() {
  datiGrafico('graficoMaterialiUsati', 'bar', {
    labels: DATI.materiali_piu_usati.map(function (m) { return m.nome; }),
    datasets: [{ label: 'Consumo storico', data: DATI.materiali_piu_usati.map(function (m) { return m.consumo; }), backgroundColor: colore('teal') }]
  }, senzaLegenda(Object.assign(opzioniBase(), { indexAxis: 'y', scales: { x: asse(), y: asse() } })));
}

function disegnaClienti() {
  datiGrafico('graficoClienti', 'bar', {
    labels: DATI.clienti.map(function (c) { return c.cliente; }),
    datasets: [{ label: 'Valore preventivi', data: DATI.clienti.map(function (c) { return c.valore; }), backgroundColor: colore('blu') }]
  }, senzaLegenda(Object.assign(opzioniBase(), { indexAxis: 'y', scales: { x: asse(), y: asse() } })));
}

function disegnaTabellaRicarico() {
  document.getElementById('corpoTabellaRicarico').innerHTML = DATI.ricarico.map(function (r) {
    return '<tr><td>' + esc(r.nome) + '</td><td class="num">' + formattaEuro(r.prezzo_vendita) +
      '</td><td class="num">' + formattaEuro(r.prezzo_fornitore) +
      '</td><td class="num">' + formattaNumero(r.ricarico_pct, 0) + '%</td></tr>';
  }).join('');
}

function disegnaStoricoPrezzi() {
  var conVariazione = DATI.magazzino
    .filter(function (m) { return m.n_rilevazioni_prezzo >= 2; })
    .sort(function (a, b) { return Math.abs(b.variazione_prezzo_pct) - Math.abs(a.variazione_prezzo_pct); });
  var inMonitoraggio = DATI.magazzino.filter(function (m) { return m.n_rilevazioni_prezzo === 1; }).length;

  document.getElementById('corpoTabellaStoricoPrezzi').innerHTML = conVariazione.map(function (m) {
    // Per un costo d'acquisto salire e' negativo: colore rosso.
    var classe = m.variazione_prezzo_pct > 0 ? 'giu' : (m.variazione_prezzo_pct < 0 ? 'su' : 'neutro');
    return '<tr><td>' + esc(m.nome) + '</td>' +
      '<td class="num">' + formattaEuro(m.primo_prezzo_rilevato) +
      ' <span class="testo-muto">(' + formattaData(m.prima_rilevazione_prezzo) + ')</span></td>' +
      '<td class="num">' + formattaEuro(m.prezzo_fornitore_medio) + '</td>' +
      '<td class="num"><span class="delta ' + classe + '">' +
      (m.variazione_prezzo_pct > 0 ? '+' : '') + formattaNumero(m.variazione_prezzo_pct, 1) +
      '%</span></td></tr>';
  }).join('');

  var nota = document.getElementById('notaStoricoPrezzi');
  if (conVariazione.length === 0) {
    nota.textContent = 'Il monitoraggio dei prezzi è appena iniziato: nessuna variazione ancora osservata su ' +
      inMonitoraggio + ' ' + plurale(inMonitoraggio, 'materiale', 'materiali') +
      '. Le variazioni compariranno qui dal prossimo cambio di prezzo: il gestionale non conserva lo storico, ' +
      'quindi gli aumenti già avvenuti in passato non sono recuperabili.';
  } else if (inMonitoraggio > 0) {
    nota.textContent = 'Altri ' + inMonitoraggio + ' ' +
      plurale(inMonitoraggio, 'materiale è monitorato ma non ha', 'materiali sono monitorati ma non hanno') +
      ' ancora mostrato variazioni di prezzo.';
  } else {
    nota.style.display = 'none';
  }
}

function disegnaTabellaFornitori() {
  if (!DATI.confronto_fornitori.length) return;
  document.getElementById('sezioneFornitori').style.display = '';
  var righe = [];
  DATI.confronto_fornitori.forEach(function (c) {
    c.fornitori.forEach(function (f, i) {
      righe.push('<tr><td>' + (i === 0 ? esc(c.nome) : '') + '</td><td>' + esc(f.fornitore) +
        '</td><td class="num">' + formattaEuro(f.prezzo) +
        '</td><td class="num">' + formattaNumero(f.giacenza, 1) +
        '</td><td class="num">' + (i === 0 ? formattaNumero(c.risparmio_pct, 0) + '%' : '') + '</td></tr>');
    });
  });
  document.getElementById('corpoTabellaFornitori').innerHTML = righe.join('');
}

function disegnaTabellaMovimenti() {
  document.getElementById('corpoTabellaMovimenti').innerHTML = DATI.movimenti.map(function (m) {
    var classe = m.tipo === 'carico' ? 'carico' : 'scarico';
    return '<tr><td>' + formattaData(m.data) + '</td><td>' + esc(m.materiale) +
      '</td><td><span class="tag ' + classe + '">' + esc(m.tipo) + '</span></td>' +
      '<td class="num">' + formattaNumero(m.quantita, 1) + '</td><td>' + esc(m.fornitore) +
      '</td><td class="testo-muto">' + esc(m.note) + '</td></tr>';
  }).join('');
}

function disegnaNotiziario() {
  var contenitore = document.getElementById('contenutoNotiziario');
  var notizie = DATI.notiziario && DATI.notiziario.notizie;
  if (!notizie || !notizie.length) {
    contenitore.innerHTML = '<div class="suggerimento">Il notiziario di settore non è ancora attivo. ' +
      'Esegui <code>notiziario.py</code> per generare <code>notiziario_ultimo.json</code>.</div>';
    return;
  }
  contenitore.innerHTML = notizie.map(function (n) {
    return '<div class="notizia"><div>' + esc(n.titolo) + '</div>' +
      (n.rilevanza ? '<div class="rilevanza">' + esc(n.rilevanza) + '</div>' : '') +
      '<div class="fonte">' + esc(n.fonte || '') + '</div></div>';
  }).join('');
}

function disegnaTutteLeSezioniStatiche() {
  disegnaGraficoGiacenze();
  disegnaGraficoAutonomia();
  disegnaTabellaMagazzino();
  disegnaConsigliMagazzino();
  disegnaManodopera();
  disegnaMaterialiUsati();
  disegnaClienti();
  disegnaTabellaRicarico();
  disegnaStoricoPrezzi();
  disegnaTabellaFornitori();
  disegnaTabellaMovimenti();
  disegnaNotiziario();
}

/* ============================================================
   4. Avvio (solo nel browser: in Node il file viene solo importato)
   ============================================================ */

function avvia() {
  document.querySelectorAll('#selettorePeriodo .pill').forEach(function (b) {
    b.addEventListener('click', function () { aggiornaTutto(b.dataset.periodo); });
  });

  var osservatore = new IntersectionObserver(function (voci) {
    voci.forEach(function (v) { if (v.isIntersecting) v.target.classList.add('visibile'); });
  }, { threshold: 0.08 });
  document.querySelectorAll('.sezione').forEach(function (s) { osservatore.observe(s); });

  aggiornaTutto('mese');
  disegnaTutteLeSezioniStatiche();

  if (typeof Chart === 'undefined') {
    document.querySelectorAll('.contenitore-grafico').forEach(function (c) {
      c.innerHTML = '<div class="suggerimento">Chart.js non è disponibile: serve una connessione a internet ' +
        'la prima volta, per scaricarlo e metterlo in cache. Numeri e tabelle restano comunque leggibili.</div>';
    });
  }
}

if (typeof document !== 'undefined') {
  avvia();
}

/* Esportazione per i test automatici (in Node). Nel browser non fa nulla. */
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    chiavePeriodo: chiavePeriodo,
    etichettaPeriodo: etichettaPeriodo,
    periodoAdiacente: periodoAdiacente,
    descrizioneConfronto: descrizioneConfronto,
    aggrega: aggrega,
    calcolaDelta: calcolaDelta,
    plurale: plurale,
    materialiSottoScorta: materialiSottoScorta,
    costruisciSintesi: costruisciSintesi,
    costruisciConsigliMagazzino: costruisciConsigliMagazzino,
    esc: esc,
    formattaEuro: formattaEuro,
    formattaNumero: formattaNumero,
    formattaData: formattaData,
    aggiungiGiorni: aggiungiGiorni,
    formattaGiorni: formattaGiorni
  };
}
