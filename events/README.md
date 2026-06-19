# Eventi mantovadev

In questa cartella troverai una cartella dedicata per ogni evento.

## Convenzione struttura

Usiamo il formato:

`YYYY-MM-DD/README.md`

Esempio:

`2026-04-09/README.md`

Nella cartella dell'evento puoi aggiungere anche materiali collegati, ad esempio codice demo, slide PDF, immagini o altri asset.

## Materiali delle presentazioni

Quando un talk ha slide, codice o altri allegati, aggiungili nella cartella dell'evento e linkali in una sezione `## Risorse` alla fine del `README.md`, usando percorsi relativi.

Esempi:

- `Slide: [slides.pdf](./slides.pdf)`
- `Demo: [demo/](./demo/)`
- `Repository: [nome-progetto](https://github.com/...)`

Se per un evento non ci sono materiali da condividere, la sezione puo anche essere omessa.

## Template

Per creare un nuovo evento:

1. crea la cartella `YYYY-MM-DD/`
2. copia il contenuto di [`_template.md`](./_template.md) in `README.md`
3. aggiungi eventuali asset nella stessa cartella e aggiorna `## Risorse` quando sono disponibili
