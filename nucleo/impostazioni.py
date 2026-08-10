"""Soglie e parametri del Cruscotto, in un posto solo.

Erano numeri fissi sparsi nel codice: cosi' si regolano senza toccare la
logica, e si possono sovrascrivere da ``config.json`` senza ricompilare
l'eseguibile:

    {
      "db_path": "...",
      "impostazioni": { "giorni_allarme_esaurimento": 21 }
    }
"""

from .utils import num


class Impostazioni:
    """Parametri di analisi. Ogni valore ha un default sensato."""

    # --- Affidabilita' della stima -----------------------------------------
    # Sotto queste soglie i movimenti registrati sono troppo pochi per
    # calcolare consumo/autonomia con un minimo di senso: il report lo dice
    # invece di mostrare un numero inventato.
    movimenti_minimi = 4
    giorni_storico_minimi = 14

    # --- Finestre di osservazione del consumo ------------------------------
    giorni_finestra_recente = 30   # "ultimo mese"
    giorni_finestra_confronto = 60  # fine della finestra precedente

    # --- Allarmi ------------------------------------------------------------
    giorni_allarme_esaurimento = 14  # sotto tanti giorni di autonomia = allarme
    giorni_attenzione_esaurimento = 30  # sotto tanti = da tenere d'occhio
    soglia_riempimento_magazzino = 0.9  # % capacita' oltre cui e' "pieno"

    # --- Riordino -----------------------------------------------------------
    # Tempo di consegna presunto quando non ci sono abbastanza carichi passati
    # per stimarlo dai dati reali.
    lead_time_default_giorni = 14

    # --- Consigli operativi -------------------------------------------------
    soglia_aumento_consumo_pct = 40.0   # oltre = "consumo in forte aumento"
    soglia_aumento_prezzo_pct = 8.0     # oltre = "prezzo fornitore salito"
    soglia_risparmio_fornitore_pct = 15.0  # oltre = "conviene cambiare"
    max_consigli_per_tipo = 3           # per non affogare i consigli concreti

    # --- Manutenzione -------------------------------------------------------
    report_da_mantenere = 30  # quanti report datati tenere nella cartella

    @classmethod
    def da_dizionario(cls, dati):
        """Costruisce le impostazioni partendo dai default e applicando le
        chiavi presenti in ``dati``. Chiavi sconosciute o di tipo sbagliato
        vengono ignorate: un config.json scritto a mano non deve rompere nulla.
        """
        imp = cls()
        if not isinstance(dati, dict):
            return imp
        for chiave, valore in dati.items():
            default = getattr(cls, chiave, None)
            if default is None or callable(default) or chiave.startswith("_"):
                continue
            if isinstance(default, bool):
                imp_valore = bool(valore)
            elif isinstance(default, int) and not isinstance(valore, bool):
                imp_valore = int(num(valore))
            elif isinstance(default, float):
                imp_valore = num(valore)
            else:
                continue
            setattr(imp, chiave, imp_valore)
        return imp
