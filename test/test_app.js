/* Test della logica del report (le funzioni pure di assets_web/app.js).

   Si eseguono con Node, senza installare nulla:

       node --test test/

   Servono soprattutto a proteggere due punti dove si sono gia' annidati
   errori: il calcolo del margine per periodo e le regole che decidono quali
   consigli operativi mostrare. */

const test = require('node:test');
const assert = require('node:assert');
const path = require('path');

const app = require(path.join(__dirname, '..', 'assets_web', 'app.js'));

/* ---------- Aiutanti ---------- */

function preventivo(data, prezzo, costoTotale) {
  return {
    data: data, prezzo: prezzo, costo_totale: costoTotale,
    costo_materiali: costoTotale * 0.6, mano_opera: costoTotale * 0.4
  };
}

function materiale(campi) {
  return Object.assign({
    nome: 'MAT', giacenza: 100, scorta_minima: 10, scorta_massima: 200,
    capacita_magazzino: 0, dati_sufficienti: true, sotto_scorta: false,
    consumo_ultimi_30gg: 30, consumo_30_60gg_fa: 30, trend_consumo_pct: 0,
    velocita_consumo_giorno: 1, velocita_prudente_giorno: 1,
    giorni_autonomia_stimati: 100, giorni_autonomia_prudente: 100,
    tempo_restock_mediano_giorni: 10, lead_time_giorni: 10,
    da_riordinare: false, quantita_da_ordinare: null, giorni_entro_cui_ordinare: null,
    n_rilevazioni_prezzo: 1, variazione_prezzo_pct: null, prima_rilevazione_prezzo: null,
    valore_giacenza: 1000, n_fornitori: 1, n_movimenti: 20
  }, campi || {});
}

const OGGI = '2026-08-10T12:00:00';

function consigli(magazzino, fornitori) {
  return app.costruisciConsigliMagazzino(magazzino, fornitori || [], OGGI, {});
}

function contiene(elenco, frammento) {
  return elenco.some(function (t) { return t.indexOf(frammento) !== -1; });
}

/* ---------- Chiavi di periodo ---------- */

test('chiavePeriodo distingue mese, trimestre e anno', () => {
  assert.strictEqual(app.chiavePeriodo('2026-07-15T10:00:00', 'mese'), '2026-07');
  assert.strictEqual(app.chiavePeriodo('2026-07-15T10:00:00', 'trimestre'), '2026-T3');
  assert.strictEqual(app.chiavePeriodo('2026-07-15T10:00:00', 'anno'), '2026');
});

test('chiavePeriodo mette gennaio nel primo trimestre', () => {
  assert.strictEqual(app.chiavePeriodo('2026-01-01T00:00:00', 'trimestre'), '2026-T1');
  assert.strictEqual(app.chiavePeriodo('2026-12-31T23:00:00', 'trimestre'), '2026-T4');
});

test('periodoAdiacente attraversa il cambio di anno', () => {
  assert.strictEqual(app.periodoAdiacente('2026-01', 'mese', -1), '2025-12');
  assert.strictEqual(app.periodoAdiacente('2025-12', 'mese', 1), '2026-01');
  assert.strictEqual(app.periodoAdiacente('2026-T1', 'trimestre', -1), '2025-T4');
  assert.strictEqual(app.periodoAdiacente('2026', 'anno', -1), '2025');
});

test('descrizioneConfronto dice sempre quale periodo si sta guardando', () => {
  // Un mese ancora senza preventivi mostra zeri: l'etichetta evita che
  // sembrino un guasto del programma.
  assert.strictEqual(
    app.descrizioneConfronto('2026-08', 'mese', true), 'Ago 2026 rispetto a Lug 2026');
  assert.ok(
    app.descrizioneConfronto('2026-08', 'mese', false).indexOf('nessun periodo precedente') !== -1);
});

test('etichettaPeriodo scrive i mesi in italiano', () => {
  assert.strictEqual(app.etichettaPeriodo('2026-07', 'mese'), 'Lug 2026');
  assert.strictEqual(app.etichettaPeriodo('2026-T3', 'trimestre'), '2026 · T3');
});

/* ---------- Aggregazione dei preventivi ---------- */

test('aggrega raggruppa e ordina i periodi', () => {
  const serie = app.aggrega([
    preventivo('2026-03-10T10:00:00', 100, 70),
    preventivo('2026-01-05T10:00:00', 200, 150),
    preventivo('2026-03-20T10:00:00', 300, 200)
  ], 'mese');
  assert.deepStrictEqual(serie.map(g => g.chiave), ['2026-01', '2026-03']);
  assert.strictEqual(serie[1].n, 2);
  assert.strictEqual(serie[1].valore, 400);
});

test('il margine e ponderato sul fatturato, non media delle percentuali', () => {
  // Un preventivo minuscolo in forte perdita non deve travolgere il mese:
  // e' il caso reale della voce di prova da 3,49 EUR con 124 EUR di costo,
  // che con la media semplice portava il margine mensile a -388%.
  const serie = app.aggrega([
    preventivo('2026-03-01T10:00:00', 1950, 1430),
    preventivo('2026-03-02T10:00:00', 3.49, 124.78)
  ], 'mese');
  const margine = serie[0].margineMedio;
  assert.ok(margine > 20 && margine < 30, 'margine ponderato atteso ~26%, ottenuto ' + margine);
});

test('aggrega non divide per zero con fatturato nullo', () => {
  const serie = app.aggrega([preventivo('2026-03-01T10:00:00', 0, 50)], 'mese');
  assert.strictEqual(serie[0].margineMedio, 0);
  assert.strictEqual(serie[0].prezzoMedio, 0);
});

test('calcolaDelta gestisce base nulla o assente', () => {
  assert.strictEqual(app.calcolaDelta(10, 0), null);
  assert.strictEqual(app.calcolaDelta(10, null), null);
  assert.strictEqual(app.calcolaDelta(150, 100), 50);
  assert.strictEqual(app.calcolaDelta(50, 100), -50);
});

/* ---------- Sotto scorta ---------- */

test('materialiSottoScorta ordina dal piu grave', () => {
  const elenco = app.materialiSottoScorta([
    materiale({ nome: 'QUASI', sotto_scorta: true, giacenza: 9, scorta_minima: 10 }),
    materiale({ nome: 'GRAVE', sotto_scorta: true, giacenza: 1, scorta_minima: 10 }),
    materiale({ nome: 'BENE', sotto_scorta: false })
  ]);
  assert.deepStrictEqual(elenco.map(m => m.nome), ['GRAVE', 'QUASI']);
});

/* ---------- Consigli operativi ---------- */

test('il riordino indica quantita e scadenza', () => {
  const c = consigli([materiale({
    nome: 'TWILL', da_riordinare: true, quantita_da_ordinare: 120,
    giorni_entro_cui_ordinare: 5, lead_time_giorni: 12
  })]);
  assert.ok(contiene(c.consigli, 'Ordina circa 120 unità di TWILL'));
  assert.ok(contiene(c.consigli, '15/08/2026'), 'attesa la data di oggi + 5 giorni');
});

test('senza margine di tempo il riordino e immediato', () => {
  const c = consigli([materiale({
    nome: 'HS300', da_riordinare: true, quantita_da_ordinare: 50, giorni_entro_cui_ordinare: 0
  })]);
  assert.ok(contiene(c.consigli, 'appena possibile'));
});

test('un consumo in crescita su base minuscola non genera allarmi', () => {
  // Da 1 a 20 unita' sono +1900%, ma su una base cosi' piccola e' rumore.
  const c = consigli([materiale({
    nome: 'RUMORE', trend_consumo_pct: 1900, consumo_30_60gg_fa: 1,
    consumo_ultimi_30gg: 20, scorta_minima: 30
  })]);
  assert.ok(!contiene(c.consigli, 'RUMORE'), 'non doveva comparire: ' + c.consigli.join(' | '));
});

test('un consumo in crescita su base solida genera il consiglio', () => {
  const c = consigli([materiale({
    nome: 'VERO', trend_consumo_pct: 120, consumo_30_60gg_fa: 98,
    consumo_ultimi_30gg: 209, scorta_minima: 30
  })]);
  assert.ok(contiene(c.consigli, 'Il consumo di VERO è cresciuto molto'));
});

test('gli aumenti di prezzo compaiono solo con almeno due rilevazioni', () => {
  const unaSola = consigli([materiale({
    nome: 'UNA', n_rilevazioni_prezzo: 1, variazione_prezzo_pct: 50
  })]);
  assert.ok(!contiene(unaSola.consigli, 'UNA'));

  const due = consigli([materiale({
    nome: 'DUE', n_rilevazioni_prezzo: 2, variazione_prezzo_pct: 20,
    prima_rilevazione_prezzo: '2026-06-01T00:00:00'
  })]);
  assert.ok(contiene(due.consigli, 'Il prezzo fornitore di DUE è salito del 20%'));
});

test('un calo di prezzo e un punto di forza, non un allarme', () => {
  const c = consigli([materiale({
    nome: 'CALO', n_rilevazioni_prezzo: 2, variazione_prezzo_pct: -15,
    prima_rilevazione_prezzo: '2026-06-01T00:00:00'
  })]);
  assert.ok(contiene(c.forza, 'sceso'));
  assert.ok(!contiene(c.consigli, 'CALO'));
});

test('i consigli sono limitati per non seppellire quelli importanti', () => {
  const molti = [];
  for (let i = 0; i < 10; i++) {
    molti.push(materiale({
      nome: 'M' + i, da_riordinare: true, quantita_da_ordinare: 10, giorni_entro_cui_ordinare: i
    }));
  }
  const c = app.costruisciConsigliMagazzino(molti, [], OGGI, { max_consigli_per_tipo: 3 });
  const riordini = c.consigli.filter(t => t.indexOf('Ordina circa') === 0);
  assert.strictEqual(riordini.length, 3);
  // I piu' urgenti per primi.
  assert.ok(riordini[0].indexOf('M0') !== -1);
});

test('i materiali senza storico sono segnalati ma non analizzati', () => {
  const c = consigli([
    materiale({ nome: 'IGNOTO', dati_sufficienti: false, da_riordinare: true, quantita_da_ordinare: 99 })
  ]);
  assert.ok(!contiene(c.consigli, 'Ordina circa 99'), 'non deve consigliare su dati insufficienti');
  assert.ok(contiene(c.consigli, 'IGNOTO'));
  assert.ok(contiene(c.consigli, 'abbastanza movimenti registrati'));
});

test('nessun problema produce messaggi rassicuranti, non liste vuote', () => {
  const c = consigli([materiale({ nome: 'TUTTOBENE' })]);
  assert.ok(contiene(c.attenzione, 'Nessun materiale mostra segnali critici'));
  assert.ok(contiene(c.consigli, 'Nessun intervento urgente'));
  assert.ok(contiene(c.forza, 'scorte sufficienti'));
});

test('il fornitore alternativo compare solo oltre la soglia di risparmio', () => {
  const sotto = consigli([materiale({})], [{ nome: 'POCO', risparmio_pct: 5 }]);
  assert.ok(!contiene(sotto.consigli, 'POCO'));
  const sopra = consigli([materiale({})], [{ nome: 'TANTO', risparmio_pct: 31 }]);
  assert.ok(contiene(sopra.consigli, 'Per TANTO un fornitore alternativo costa il 31% in meno'));
});

test('il magazzino quasi pieno segnala il capitale fermo', () => {
  const c = consigli([materiale({
    nome: 'PIENO', giacenza: 95, capacita_magazzino: 100, valore_giacenza: 2500
  })]);
  assert.ok(contiene(c.consigli, 'PIENO è quasi al limite di capienza'));
});

/* ---------- Sintesi ---------- */

test('la sintesi confronta col periodo precedente', () => {
  const corrente = { valore: 1500, margineMedio: 30 };
  const precedente = { valore: 1000, margineMedio: 25 };
  const s = app.costruisciSintesi(corrente, precedente, [materiale({})]);
  assert.ok(contiene(s.forza, 'valore preventivato è salito'));
  assert.ok(contiene(s.forza, 'margine medio è migliorato'));
});

test('la sintesi dichiara quando manca il confronto', () => {
  const s = app.costruisciSintesi({ valore: 100, margineMedio: 10 }, null, [materiale({})]);
  assert.ok(contiene(s.cambi, 'Ancora nessun periodo precedente'));
});

test('un calo di fatturato finisce fra i punti di attenzione', () => {
  const s = app.costruisciSintesi(
    { valore: 500, margineMedio: 10 }, { valore: 1000, margineMedio: 30 }, [materiale({})]);
  assert.ok(contiene(s.attenzione, 'è sceso'));
  assert.ok(contiene(s.attenzione, 'margine medio è peggiorato'));
});

/* ---------- Formattazione e sicurezza ---------- */

test('esc neutralizza il codice nei nomi inseriti a mano', () => {
  // Nomi di materiali e clienti sono testo libero scritto nel gestionale.
  const pericoloso = '<script>alert("x")</script>';
  const pulito = app.esc(pericoloso);
  assert.ok(pulito.indexOf('<script>') === -1);
  assert.ok(pulito.indexOf('&lt;script&gt;') !== -1);
});

test('esc gestisce valori mancanti', () => {
  assert.strictEqual(app.esc(null), '');
  assert.strictEqual(app.esc(undefined), '');
  assert.strictEqual(app.esc(0), '0');
});

test('formattaData mostra un trattino quando la data manca o e rotta', () => {
  assert.strictEqual(app.formattaData(null), '—');
  assert.strictEqual(app.formattaData(''), '—');
  assert.strictEqual(app.formattaData('non-una-data'), '—');
  assert.strictEqual(app.formattaData('2026-08-10T12:00:00'), '10/08/2026');
});

test('formattaGiorni distingue lo zero dal dato assente', () => {
  assert.strictEqual(app.formattaGiorni(null), '—');
  assert.strictEqual(app.formattaGiorni(0), '0 gg');
  assert.strictEqual(app.formattaGiorni(12), '12 gg');
});

test('aggiungiGiorni attraversa il cambio di mese', () => {
  const risultato = app.aggiungiGiorni('2026-08-28T12:00:00Z', 5);
  assert.strictEqual(risultato.slice(0, 10), '2026-09-02');
});

test('plurale concorda i sostantivi', () => {
  assert.strictEqual(app.plurale(1, 'materiale', 'materiali'), 'materiale');
  assert.strictEqual(app.plurale(3, 'materiale', 'materiali'), 'materiali');
});
