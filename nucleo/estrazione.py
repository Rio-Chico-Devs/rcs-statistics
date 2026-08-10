"""Lettura del database del gestionale RCS.

Ogni funzione ``estrai_*`` esegue una query e restituisce ``list[dict]`` di
valori gia' puliti (float arrotondati, mai ``None`` al posto di un numero,
date in formato ISO): il resto del programma non vede mai righe grezze di
sqlite. Nessuna funzione qui scrive: la connessione e' aperta in sola lettura.
"""

from datetime import datetime

from .analisi import analizza_movimenti, stato_materiale
from .impostazioni import Impostazioni
from .utils import log, num, parse_data


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
    scartati = 0
    for r in cur.fetchall():
        data = parse_data(r["data_creazione"])
        if data is None:
            scartati += 1
            continue
        prezzo = num(r["prezzo_cliente"])
        costo_materiali = num(r["costo_totale_materiali"])
        mano_opera = num(r["tot_mano_opera"])
        costo_totale = costo_materiali + num(r["costi_accessori"]) + mano_opera
        righe.append(
            {
                "id": r["id"],
                "data": data.isoformat(),
                "cliente": r["nome_cliente"] or "",
                "categoria": r["categoria"] or "",
                "prezzo": round(prezzo, 2),
                "costo_materiali": round(costo_materiali, 2),
                "mano_opera": round(mano_opera, 2),
                "costo_totale": round(costo_totale, 2),
            }
        )
    if scartati:
        log("estrai_preventivi: %d preventivi saltati per data non valida" % scartati)
    return righe


def _movimenti_per_materiale(conn):
    """Movimenti raggruppati per materiale, con date gia' convertite.

    Le righe con data illeggibile vengono saltate e annotate: un singolo dato
    sporco nel gestionale non deve impedire la generazione dell'intero report.
    """
    cur = conn.execute(
        "SELECT materiale_id, tipo, quantita, data FROM movimenti_magazzino ORDER BY materiale_id, data"
    )
    per_materiale = {}
    scartati = 0
    for r in cur.fetchall():
        data = parse_data(r["data"])
        if data is None:
            scartati += 1
            continue
        per_materiale.setdefault(r["materiale_id"], []).append(
            {"data": data, "tipo": r["tipo"], "quantita": num(r["quantita"])}
        )
    if scartati:
        log("movimenti_magazzino: %d righe saltate per data non valida" % scartati)
    return per_materiale


def estrai_magazzino(conn, oggi=None, imp=None):
    """Una riga per materiale: giacenza, soglie, analisi di consumo e riordino.

    La giacenza vera e' la somma per fornitore (``materiale_fornitori``): la
    colonna ``materiali.giacenza`` nel database del gestionale resta sempre a
    zero e non va usata.

    Funzione di sola lettura, senza effetti collaterali: lo storico prezzi
    (che scrive su disco) e' gestito a parte.
    """
    imp = imp or Impostazioni()
    oggi = oggi or datetime.now()

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

    movimenti_per_materiale = _movimenti_per_materiale(conn)

    righe = []
    for m in materiali:
        forn = fornitori_per_materiale.get(m["id"], [])
        giacenza = round(sum(num(f["giacenza"]) for f in forn), 2)
        scorta_minima = round(num(m["scorta_minima"]), 2)
        scorta_massima = round(num(m["scorta_massima"]), 2)
        capacita = num(m["capacita_magazzino"]) or num(m["cat_capacita"]) or scorta_massima

        # Solo i fornitori con un prezzo reale (>0): una riga fornitore a
        # prezzo zero (capita nel gestionale) abbasserebbe la media e
        # falserebbe capitale a magazzino e storico prezzi.
        prezzi_validi = [num(f["prezzo_fornitore"]) for f in forn if num(f["prezzo_fornitore"]) > 0]
        prezzo_medio = round(sum(prezzi_validi) / len(prezzi_validi), 2) if prezzi_validi else 0.0

        # Capitale immobilizzato esatto: ogni lotto vale il prezzo del suo
        # fornitore, non un prezzo medio applicato al totale.
        valore_giacenza = round(
            sum(num(f["giacenza"]) * num(f["prezzo_fornitore"]) for f in forn), 2
        )

        analisi = analizza_movimenti(
            movimenti_per_materiale.get(m["id"], []),
            giacenza_attuale=giacenza,
            oggi=oggi,
            scorta_minima=scorta_minima,
            scorta_massima=scorta_massima,
            imp=imp,
        )
        # Le date servono al report come stringhe ISO.
        if analisi.get("ultimo_restock") is not None:
            analisi["ultimo_restock"] = analisi["ultimo_restock"].isoformat()

        riga = {
            "materiale_id": m["id"],
            "nome": m["nome"],
            "giacenza": giacenza,
            "scorta_minima": scorta_minima,
            "scorta_massima": scorta_massima,
            "capacita_magazzino": round(capacita, 2),
            "prezzo_vendita": round(num(m["prezzo"]), 2),
            "prezzo_fornitore_medio": prezzo_medio,
            "valore_giacenza": valore_giacenza,
            "n_fornitori": len(forn),
            "sotto_scorta": scorta_minima > 0 and giacenza < scorta_minima,
        }
        riga.update(analisi)
        # Lo stato (OK / Da riordinare / Sotto scorta / ...) e' una regola di
        # business: si decide qui una volta sola, non nel JavaScript.
        classe, testo = stato_materiale(riga, imp)
        riga["stato_classe"] = classe
        riga["stato_testo"] = testo
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
        gruppo = per_materiale.setdefault(
            r["materiale_id"], {"nome": r["materiale_nome"], "fornitori": []}
        )
        gruppo["fornitori"].append(
            {
                "fornitore": r["fornitore_nome"],
                "prezzo": round(num(r["prezzo_fornitore"]), 2),
                "giacenza": round(num(r["giacenza"]), 2),
            }
        )

    righe = []
    for dati in per_materiale.values():
        if len(dati["fornitori"]) < 2:
            continue
        prezzi = [f["prezzo"] for f in dati["fornitori"] if f["prezzo"] > 0]
        risparmio_pct = (
            round((max(prezzi) - min(prezzi)) / max(prezzi) * 100, 1) if len(prezzi) >= 2 else 0.0
        )
        righe.append(
            {"nome": dati["nome"], "fornitori": dati["fornitori"], "risparmio_pct": risparmio_pct}
        )
    righe.sort(key=lambda r: r["risparmio_pct"], reverse=True)
    return righe


def estrai_movimenti(conn, limite=40):
    cur = conn.execute(
        """
        SELECT mm.data, mm.tipo, mm.quantita, mm.fornitore_nome, mm.note,
               m.nome AS materiale_nome
        FROM movimenti_magazzino mm
        JOIN materiali m ON m.id = mm.materiale_id
        ORDER BY mm.data DESC
        LIMIT ?
        """,
        (limite,),
    )
    righe = []
    for r in cur.fetchall():
        data = parse_data(r["data"])
        righe.append(
            {
                "data": data.isoformat() if data else "",
                "tipo": r["tipo"],
                "quantita": round(num(r["quantita"]), 2),
                "fornitore": r["fornitore_nome"] or "",
                "materiale": r["materiale_nome"],
                "note": r["note"] or "",
            }
        )
    return righe


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
        "Taglio": num(r["taglio"]),
        "Avvolgimento": num(r["avvolgimento"]),
        "Pulizia": num(r["pulizia"]),
        "Rettifica": num(r["rettifica"]),
        "Imballaggio": num(r["imballaggio"]),
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
    return [{"nome": r["nome"], "consumo": round(num(r["consumo"]), 2)} for r in cur.fetchall()]


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
        {
            "cliente": r["nome_cliente"],
            "valore": round(num(r["valore"]), 2),
            "n_preventivi": r["n_preventivi"],
        }
        for r in cur.fetchall()
    ]


def estrai_ricarico(conn):
    cur = conn.execute(
        """
        SELECT m.nome, m.prezzo,
               (SELECT AVG(prezzo_fornitore) FROM materiale_fornitori mf
                 WHERE mf.materiale_id = m.id AND mf.prezzo_fornitore > 0) prezzo_fornitore
        FROM materiali m
        ORDER BY m.nome
        """
    )
    righe = []
    for r in cur.fetchall():
        prezzo = num(r["prezzo"])
        costo = num(r["prezzo_fornitore"])
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
