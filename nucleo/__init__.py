"""Nucleo del Cruscotto Aziendale RCS.

Il codice e' diviso per responsabilita', in modo che ogni pezzo si possa
leggere e testare da solo:

- ``utils``          funzioni di servizio (numeri, date, percorsi, log)
- ``impostazioni``   soglie e parametri, sovrascrivibili da config.json
- ``analisi``        matematica pura sul magazzino (nessun I/O, testabile)
- ``estrazione``     lettura del database del gestionale (sola lettura)
- ``storico_prezzi`` archivio prezzi fornitore proprio del Cruscotto
- ``render``         assemblaggio del file HTML finale
"""
