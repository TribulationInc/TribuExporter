# Guida utente TribuExporter

[README](../README.md) · [English guide](USER_GUIDE.md)

TribuExporter esporta la geometria di un solido Fusion 360 in un pezzo `.tcn`
per TpaCAD. È pensato per pannelli e componenti in multistrato nei quali il
materiale rimosso è già rappresentato nel corpo BRep finale.

L'esportatore crea geometria selezionabile. Setup, utensili, compensazione,
passate in profondità, entrate e uscite, sequenza e ogni altra scelta CAM
rimangono in TpaCAD. I fori ciechi semplici nativi sono l'unica eccezione
esplicita e facoltativa: se abilitati, diventano lavorazioni foro TPA.
È inoltre possibile aggiungere una singola operazione CAM Fusion 3 assi già
calcolata tramite una seconda opzione, disattivata per impostazione predefinita.

## Prima dell'esportazione

- Usa un corpo solido, non uno sketch o una mesh STL.
- Verifica che il corpo rappresenti il pezzo finito.
- Decidi quale superficie fisica diventerà `SIDE1` in TPA.
- Scegli quale bordo deve definire la direzione X positiva del grezzo.
- Lascia deselezionate le regioni non supportate o non necessarie.

## Definire il riferimento del pannello

Avvia **Utilities → Export TpaCAD Geometry**, poi seleziona:

1. **SIDE#1** — la faccia piana principale e il corpo da esportare. La sua
   normale uscente definisce `+Z` del pannello.
2. **P0** — il punto di riferimento del sistema di lavorazione.
3. **PX** — un punto che definisce la direzione `P0 → PX`. Dopo la proiezione
   su SIDE1, questa direzione diventa `+X`.
4. **PY** — un punto che sceglie soltanto il verso positivo di `Y`. Non obbliga
   Y a seguire un eventuale bordo non ortogonale del modello.

Il sistema creato è sempre ortogonale e destrorso. Gli assi globali di Fusion e
la posizione del componente nell'assieme non definiscono l'orientamento TPA.

## Opzioni di esportazione

### Facce fittizie (SIDE7+)

Seleziona soltanto facce BRep piane inclinate che servono davvero come sistemi
di coordinate di lavorazione in TpaCAD. Ogni faccia selezionata riceve una
faccia TPA aggiuntiva, a partire da `SIDE7`, con un riferimento P0/P1/P2 esatto
e destrorso e con i propri contorni rifilati a `Z=0` locale.

Non selezionare automaticamente ogni faccia inclinata. Una superficie ottenuta
con lama o lavorazione orientata potrà essere rappresentata meglio dalla reale
operazione di produzione.

### Grezzo

**Stock allowance each side** aggiunge il sovrametallo al perimetro del corpo
nell'orientamento scelto. La geometria finita viene traslata dentro il grezzo:
non viene mai stirata, ruotata o corretta per adattarla.

Usa **Actual stock width/height** quando vuoi dichiarare un foglio reale più
grande. Zero significa usare la dimensione minima calcolata.

### Tolleranza delle curve

La tolleranza cordale predefinita di `0.01 mm` si applica soltanto alle curve
piane limitate che non possono essere scritte come primitive TPA native e
devono quindi essere linearizzate.

- Le rette rimangono rette esatte.
- Archi e cerchi rimangono archi e cerchi esatti.
- I punti finali non vengono mai spostati.
- La tolleranza non viene mai allargata senza avviso.

Le coordinate sono calcolate in millimetri e scritte con quattro decimali; la
quantizzazione massima è inferiore a `0.00005 mm`.

### Filtro dei contorni SIDE1 duplicati

**Suppress SIDE1 Z=0 loop when identical deeper loop exists** evita di scrivere
due volte un contorno di riferimento sul piano superiore quando un profilo
SIDE1 più profondo possiede lo stesso identico contorno XY completo. Il filtro
agisce soltanto sulla scrittura TCN: non modifica il modello geometrico e non
rimuove mai il profilo esterno finito obbligatorio.

### Fori ciechi nativi di Fusion

**Export native Fusion simple blind holes (W#81 CAM)** è disattivato per
impostazione predefinita. Se abilitato, l'esportatore considera esclusivamente
gli oggetti `HoleFeature` nativi della timeline che modificano il corpo
selezionato. Un cilindro BRep simile a un foro, un corpo importato, un cerchio
DXF o un taglio estruso non viene mai interpretato automaticamente come foro.

Il primo caso supportato è un foro cieco semplice, non filettato e definito da
una distanza. La faccia BRep reale di ingresso determina la SIDE TPA reale o
fittizia selezionata; il centro viene trasformato nelle coordinate XY locali e
la profondità viene scritta come Z negativo verso l'interno. Through All,
lamature, svasature, filettature, giochi, ingresso ambiguo, SIDE2 e gli altri
casi non supportati vengono segnalati e omessi.

Questa opzione scrive lavorazioni punto TPA `W#81` eseguibili. Dichiara il
diametro, ma non emette mai la selezione utensile `#205`. Prima dell'esecuzione
verifica sempre SIDE, centro, diametro e profondità negativa in TpaCAD. Lo stato
della casella viene salvato sul corpo Fusion dopo un'esportazione riuscita.

#### Fori ripetuti: usare una serie di punti nello sketch

Se vuoi esportare i fori come W#81, non applicare la serie Rettangolare,
Circolare o su Percorso al HoleFeature già completato. Fusion mantiene soltanto
il foro originale come HoleFeature nativo e rappresenta le copie come elementi
di un PatternFeature. TribuExporter sceglie deliberatamente di non trasformare
queste copie in lavorazioni foro.

Procedura supportata:

1. Crea il punto dello sketch che definisce la posizione del foro.
2. Crea la serie dei punti direttamente nello sketch.
3. Crea un unico HoleFeature nativo selezionando tutti i punti risultanti.

TribuExporter legge tutte le posizioni appartenenti a quel HoleFeature nativo
e può emettere una lavorazione W#81 per ogni punto. I bordi generati da una
serie di feature non supportata rimangono normale geometria BRep del corpo
finito e possono quindi comparire nella checklist dei profili opzionali, spesso
su una faccia laterale. Lasciali deselezionati quando non devono diventare
geometria di contorno.

### Un percorso CAM Fusion opzionale

**Export CAM toolpath** è disattivato per impostazione predefinita. Finché è
disattivato, TribuExporter non interroga Manufacture e il TCN geometrico rimane
identico. Se lo abiliti, scegli una sola operazione di fresatura già calcolata,
valida, aggiornata e non soppressa dalla lista **CAM toolpath**. Il primo caso
supportato dal comando geometria comprende 3D Contour e Parallel Fusion a 3
assi, con assi del setup e grezzo coerenti con il riferimento pannello Tribu.
Il comando autonomo in Manufacture permette di sperimentare con altre
operazioni di fresatura 3 assi già calcolate.

I movimenti risolti del centro utensile vengono aggiunti a `SIDE1` come un
unico profilo aperto indipendente L01/A01. Gli archi XY nativi di Fusion a Z
costante rimangono A01; un cerchio completo viene diviso in quattro A01 da 90°.
Le eliche XY native a raggio costante diventano A01 con sviluppo elicoidale.
Le spirali XY restano tipizzate nel `.tribupath` fino alla linearizzazione
esplicita. Gli altri archi non supportati vengono linearizzati dal post Fusion
con la tolleranza scelta.

La finestra CAM espone separatamente ogni approssimazione:

- **Post linearization tolerance** controlla la linearizzazione del post e
  quella delle spirali; non viene mai modificata automaticamente.
- **Fit fallback XY line chains to A01** abilita il fitting opzionale, con una
  tolleranza dedicata.
- **Merge exactly collinear motions** elimina soltanto punti matematicamente
  ridondanti con stessa classe e avanzamento, senza approssimazione.
- **Simplify 3D line chains** è facoltativo e inizialmente disattivato. La sua
  tolleranza limita la deviazione punto-corda misurata. Confini tra movimenti,
  estremi Z e svolte di almeno 15 gradi restano invariati.
- **Warn above complete TCN lines** genera soltanto un avviso: l'esportazione
  resta possibile e nessuna tolleranza viene allargata.
- **Reset CAM parameters to safe defaults** ripristina tutti i valori di questa
  sezione CAM senza rigenerare o modificare l'operazione Fusion selezionata.

La finestra mostra anche lo smoothing Fusion. Se indica `redistribute`/Evenly
spaced points, rigenera l'operazione con **Fit arcs** quando appropriato.

Nel TCN non vengono scritti W#89, utensile, compensazione, avanzamento o
strategia. Avanzamenti e classi Fusion restano informazioni interne usate per
continuità e diagnostica. In TpaCAD applica un solo setup all'intera traiettoria.
Il file intermedio `.tribupath` vive soltanto in una cartella temporanea durante
l'esportazione e viene eliminato automaticamente.

Per percorsi lunghi puoi invece usare manualmente in Fusion il post Tribu e
salvare un `.tribupath` permanente. Dalla cartella del repository convertilo
fuori da Fusion:

```powershell
python tribu_cam_convert.py "C:\percorso\operazione.tribupath"
```

Viene creato `operazione_TRIBU_CAM.tcn` accanto al file di ingresso, con la
stessa conservazione delle primitive native e la stessa compressione
configurabile, senza occupare il processo Python/UI di Fusion. Esegui
`python tribu_cam_convert.py --help` per tutte le opzioni. Non vengono aggiunti
W#89 o utensili.

Per il flusso autonomo originale, seleziona una sola operazione già calcolata
nel browser Manufacture e avvia **Export Selected CAM Toolpath to TCN** da
Manufacture → Utilities → Add-Ins. Questo comando scrive soltanto la
traiettoria, senza i profili geometrici BRep, e mantiene anche il supporto alle
operazioni di fresatura 3 assi già provate, come Adaptive.

## Scegliere i profili

Dopo aver completato SIDE1, P0, PX e PY, si popola la lista
**Profiles to export**.

- Il contorno esterno finito dell'intero corpo è obbligatorio.
- I nuovi candidati SIDE1 sono inizialmente selezionati.
- I nuovi candidati laterali SIDE3–SIDE6 sono inizialmente disattivati.
- Le facce inclinate selezionate vengono emesse su SIDE7+.
- Dopo un'esportazione riuscita, impostazioni numeriche e selezioni vengono
  salvate come attributi del corpo Fusion e riproposte all'esportazione
  successiva.

L'accessibilità stabilisce quale lato TPA può lavorare una faccia; la checklist
stabilisce se quella geometria serve nel programma corrente. Due facce BRep non
vengono unite solo perché hanno la stessa profondità o estremi coincidenti.

## Risultato in TpaCAD

![Profili indipendenti esportati in TpaCAD](images/tpacad-independent-profiles.png)

Nell'esempio sono presenti il contorno esterno finito, una cava a forma di T e
un'apertura per maniglia. Ogni contorno accettato inizia come profilo TPA
indipendente: l'operatore può selezionarlo e applicare il proprio setup senza
collegamenti indesiderati con i profili vicini.

Le facce laterali reali usano le normali coordinate locali di TpaCAD:

```text
-Y → SIDE3    +X → SIDE4    +Y → SIDE5    -X → SIDE6
```

Sulle facce laterali, Y locale va dal fondo del grezzo (`0`) alla parte
superiore (`DS`), mentre Z locale negativo rappresenta la profondità verso
l'interno. Le facce fittizie compaiono in aggiunta alle sei facce standard come
`SIDE7+`.

## Controllo obbligatorio

Prima di creare lavorazioni eseguibili:

1. Confronta `DL`, `DH` e `DS` con il grezzo reale.
2. Apri ogni SIDE popolata e controllane l'orientamento locale.
3. Confronta il contorno esterno obbligatorio con l'intero corpo Fusion.
4. Seleziona ogni contorno separatamente e verifica che non vengano evidenziati
   profili estranei.
5. Controlla ogni quota Z/profondità.
6. Controlla le curve linearizzate rispetto alla tolleranza dichiarata.
7. Verifica che non siano state esportate regioni inattese o non supportate.
8. Se hai abilitato i fori nativi, controlla SIDE, X, Y, profondità negativa e
   diametro di ogni W#81; verifica che nessuna geometria solo simile a un foro
   sia diventata una lavorazione.
9. Assegna la tecnologia in TpaCAD ed esegui la normale simulazione e tutti i
   controlli di sicurezza della macchina.
10. Se hai abilitato il CAM, verifica operazione scelta, traiettoria completa,
    transizioni Z, conteggi L01/A01, tolleranze, soglia di avviso e deviazione
    massima dichiarata prima di applicare un unico setup. Prima dell'uso in
    produzione, valida l'A01 elicoidale con round-trip TpaCAD e sulla Busellato.

Fermati se una dimensione del grezzo, un'assegnazione di faccia, un contorno,
una profondità o un orientamento non corrisponde al modello Fusion.
