# Guida alla programmazione lama — Busellato Jet Master T / TpaCAD

## 1. Scopo

Questa guida descrive le lavorazioni lama già configurate sul PPC Busellato:
**LAMATA X (BLADEX)**, **LAMATA Y (BLADEY)** e **LAMATA XY (BLADEXY)**. Servono
per rifilare un pannello lungo X o Y e per eseguire tagli obliqui o inclinati
mediante gli angoli A e Beta. La configurazione visibile richiama la macro
`lame.tmcr`, ma per l'operatore fa fede il contratto dei campi mostrati da
TpaCAD, non il contenuto di copie esterne della macro. [SOURCE:
`C:\TpaCAD\TpaCadCfg\CUSTOM\DBWCUST.WCAD`, definizioni BLADEX/BLADEY/BLADEXY]

I programmi reali del progetto confermano l'uso di BLADEXY per tagli inclinati,
con utensili 9600 e 3000, correzione laterale e, dove richiesto, seconda quota
Z2. [SOURCE: `010-Divisorio verticale.tcn`, “taglio con lama30”; SOURCE:
`001-Fianco sinistro.tcn`, “taglio di testa”; SOURCE: `A 53.70 deg 72.10 deg
lamate.tcn`, lavorazioni W#1052]

## 2. Prima di programmare

1. Controllare le dimensioni pezzo **DL, DH e DS** e l'unità di misura.
2. Verificare la faccia attiva. I programmi reali esaminati collocano la lamata
   su SIDE1; BLADEX e BLADEY sono configurate per le facce 1 e 2, mentre la
   definizione installata di BLADEXY non mostra lo stesso limite. [SOURCE:
   `DBWCUST.WCAD`, attributi `limins` delle tre lavorazioni; SOURCE:
   `010-Divisorio verticale.tcn`, sezione SIDE#1]
3. Controllare orientamento del grezzo, lato finito e lato di scarto.
4. Verificare che utensile, gruppo e tecnologia richiesti siano disponibili
   nella configurazione macchina. I campi tecnologici vengono risolti tramite
   l'ambiente macchina e la tabella utensili. [SOURCE: `TpaCAD_ita.pdf`, pagina
   PDF 296, parametri di sistema e tecnologia; SOURCE: `DBWCUST.WCAD`, campi
   Macchina/Gruppo/Elettromandrino/Utensile]
5. Controllare attrezzaggio, appoggi, ventose e libertà della corsa con le
   procedure previste per la macchina.
6. Verificare anteprima grafica, quote, verso, inclinazione e correzione; quindi
   compilare il programma e risolvere ogni errore prima delle normali verifiche
   macchina.

La documentazione Jet Master disponibile nel materiale esaminato non definisce
questi campi personalizzati. Per essi valgono la configurazione installata, i
programmi realmente eseguiti e le verifiche della macchina.

## 3. Quale lavorazione scegliere

| Nome sul PPC | Nome TPA | Uso confermato |
|---|---|---|
| LAMATA X | BLADEX | Taglio rettilineo parallelo all'asse X; si indicano X iniziale, X finale e la quota trasversale Qy. |
| LAMATA Y | BLADEY | Taglio rettilineo parallelo all'asse Y; si indicano Y iniziale, Y finale e la quota trasversale Qx. |
| LAMATA XY | BLADEXY | Taglio rettilineo in una direzione qualsiasi del piano XY, definita da punto iniziale, angolo A e modulo U; Beta inclina la lama. |

[SOURCE: `DBWCUST.WCAD`, descrizioni e parametri BLADEX/BLADEY/BLADEXY; SOURCE:
`Workings_ita.pdf`, pagine PDF 44–48, LAMA X/LAMA Y/LAMA XY]

Per una semplice squadratura parallela agli assi scegliere BLADEX o BLADEY. Per
una direzione obliqua in pianta, oppure per il taglio inclinato degli esempi
reali, scegliere BLADEXY.

## 4. Campi della lavorazione

La colonna “certezza” separa quanto è confermato dalla documentazione o dalla
macchina da ciò che dipende dalla configurazione Busellato.

| Campo TpaCAD | Significato | Unità | Come si sceglie | Effetto geometrico | Note Busellato | Certezza ed evidenza |
|---|---|---:|---|---|---|---|
| X iniziale (Ps) | X del punto iniziale | mm | Quota nel sistema della faccia | Posiziona l'inizio in X | In BLADEX è l'inizio dell'asse di taglio; in BLADEXY è parte del punto XY | **CONFIRMED** [SOURCE: `Workings_ita.pdf`, pp. PDF 44, 48; SOURCE: `DBWCUST.WCAD`, campo X] |
| Y iniziale (Ps) | Y del punto iniziale | mm | Quota nel sistema della faccia | Posiziona l'inizio in Y | In BLADEY è l'inizio dell'asse di taglio; in BLADEXY è parte del punto XY | **CONFIRMED** [SOURCE: `Workings_ita.pdf`, pp. PDF 46, 48; SOURCE: `DBWCUST.WCAD`, campo Y] |
| Zp | Quota/profondità programmata della lama | mm | Da un caso macchina già verificato o da procedura aziendale | Determina la penetrazione secondo il riferimento della lavorazione | Non trattarla come semplice profondità verticale; vedere §6 | **CONFIRMED come campo; MACHINE-SPECIFIC come riferimento** [SOURCE: `DBWCUST.WCAD`, campo Zp; SOURCE: `010-Divisorio verticale.tcn`, W#1052] |
| Angolo (A°) | Direzione della corsa nel piano XY | gradi | In base al verso richiesto | Ruota la direzione da +X | 90° e 270° sono versi opposti e non vanno scambiati automaticamente | **CONFIRMED** [SOURCE: `Workings_ita.pdf`, p. PDF 48; SOURCE: `001-Fianco sinistro.tcn` + `010-Divisorio verticale.tcn`] |
| Modulo (U) | Lunghezza della corsa lungo A | mm | Sufficiente a coprire tutto il materiale | Fissa il punto finale a distanza U | Può estendersi oltre il grezzo | **CONFIRMED** [SOURCE: `Workings_ita.pdf`, p. PDF 48; SOURCE: `001-Fianco sinistro.tcn`, U=1140] |
| Utensile | Identificatore tecnologico usato dalla lavorazione | ID | Selezionare l'utensile lama configurato | Carica geometria e tecnologia associate | Nei programmi compaiono 9600 e 3000; non sostituirli tra loro | **CONFIRMED** [SOURCE: `DBWCUST.WCAD`, campo Utensile; SOURCE: corpus TCN reale] |
| Vel. rotazione | Regime mandrino | configurazione macchina, normalmente rpm | Valore ammesso dalla tecnologia utensile | Imposta o sovrascrive il regime | Se omesso può valere il default tecnologico | **CONFIRMED come campo; MACHINE-SPECIFIC come valore** [SOURCE: `Workings_ita.pdf`, pp. PDF 44–48; SOURCE: `DBWCUST.WCAD`, campo S] |
| Vel. ingresso | Avanzamento di ingresso | configurazione macchina | Usare la tecnologia approvata | Regola la fase di ingresso | Le unità di avanzamento dipendono dalla configurazione | **CONFIRMED come campo; MACHINE-SPECIFIC come unità/valore** [SOURCE: `DBWCUST.WCAD`, campo F; SOURCE: `TpaCAD_ita.pdf`, p. PDF 296] |
| Vel. movimento | Avanzamento lungo il taglio | configurazione macchina | Usare la tecnologia approvata | Regola il movimento di lavoro | Non trasferire automaticamente valori da un catalogo Fusion | **CONFIRMED come campo; MACHINE-SPECIFIC come unità/valore** [SOURCE: `DBWCUST.WCAD`, campo FI; SOURCE: `TpaCAD_ita.pdf`, p. PDF 296] |
| Angolo Beta (°) | Brandeggio/inclinazione della lama | gradi | Dalla geometria verificata del taglio | Inclina l'asse utensile rispetto alla corsa/setup | Segno e riferimento dipendono dalla configurazione | **CONFIRMED** [SOURCE: `Workings_ita.pdf`, pp. PDF 44–48; SOURCE: `TpaCAD_ita.pdf`, pp. PDF 126–127] |
| Calcola corda | Richiede il riferimento alla corda d'ingresso per lama inclinata | On/Off | Lasciare Off per gli esempi rettilinei qui documentati | Cambia il riferimento di posizionamento previsto dalla lavorazione | Tutti gli esempi reali esaminati hanno Off | **CONFIRMED** [SOURCE: `Workings_ita.pdf`, pp. PDF 44–48; SOURCE: corpus W#1052 reale, campo #8527=0] |
| Correzione | Scelta Off/Sx/Dx | scelta | In base a lato finito, scarto e verso | Sposta il lato utile del taglio rispetto alla traiettoria | Sx/Dx dipendono dal verso A; vedere §7 | **CONFIRMED** [SOURCE: `DBWCUST.WCAD`, Correzione Off/Sx/Dx; SOURCE: `001-Fianco sinistro.tcn` + `002-Fianco destro.tcn`] |
| Abilita Z2 | Attiva la seconda quota di lavorazione | On/Off | On solo per una passata Z2 prevista e verificata | Fa usare anche Z2 | Il caso reale ha Zp=-3 e Z2=-28,07 | **CONFIRMED** [SOURCE: `Workings_ita.pdf`, pp. PDF 45, 47–48; SOURCE: `001-Fianco sinistro.tcn`] |
| Z2 | Seconda quota di profondità | mm | Inserire la quota prevista dal ciclo verificato | Aggiunge la seconda passata/profondità | Nel ciclo validato segue la passata superficiale Zp | **CONFIRMED per questa configurazione** [SOURCE: `001-Fianco sinistro.tcn`, #8526=1 e #8513=-28.07; DERIVED: programma reale + validazione CNC del progetto] |
| Velocità Z2 | Velocità associata alla passata intermedia/seconda | configurazione macchina | Solo se richiesta dalla tecnologia | Modifica l'avanzamento della passata Z2 | I TCN campione non la impostano; resta alla tecnologia | **CONFIRMED come campo; UNKNOWN nel campione** [SOURCE: `Workings_ita.pdf`, pp. PDF 45, 47–48; SOURCE: `DBWCUST.WCAD`, campo FZ] |

## 5. Sistema geometrico

### X/Y iniziale

In BLADEXY, X iniziale e Y iniziale formano il punto Ps nella SIDE attiva. La
corsa parte da Ps e segue la direzione A per la distanza U. [SOURCE:
`Workings_ita.pdf`, p. PDF 48, LAMA XY]

```text
Y
^          Pe
|         /
|        /  U
|       /
|     Ps ----> riferimento +X
+------------------------------> X
        A
```

### Angolo A

A è misurato nel piano XY rispetto a +X. Nei programmi reali, A=90° percorre
la direzione Y crescente: partendo da Y=-180 con U=1140 termina geometricamente
a Y=960. A=270° percorre invece Y decrescente. [SOURCE: `Workings_ita.pdf`, p.
PDF 48; DERIVED: `001-Fianco sinistro.tcn` + `010-Divisorio verticale.tcn`]

```text
          A=90°
            ↑
A=180°  ←   +   →  A=0°
            ↓
          A=270°
```

I due versi non sono intercambiabili: cambiano punto di partenza, ingresso e
relazione Sx/Dx con il lato finito.

### Modulo U

U è la lunghezza programmata lungo A. Nel fianco sinistro, Y=-180, A=90° e
U=1140 coprono tutto DH=780 e proseguono fino a Y=960, cioè 180 mm oltre ogni
estremità. Il programma dimostra l'estensione, ma non stabilisce una regola
universale per calcolarla. [SOURCE: `001-Fianco sinistro.tcn`, intestazione
`DH=780` e W#1052]

### Beta

Alpha/A orienta la corsa in pianta; Beta inclina la lama rispetto a quella
direzione e al setup. La documentazione TPA avverte che asse e segno possono
dipendere dalla configurazione. [SOURCE: `TpaCAD_ita.pdf`, pp. PDF 126–127,
geometrie orientate; SOURCE: `Workings_ita.pdf`, pp. PDF 44–48]

Beta=51,97° e Beta=60,46° sono valori realmente usati su questa Busellato. Non
sono valori predefiniti né ricette trasferibili a un altro pannello. [SOURCE:
`010-Divisorio verticale.tcn`; SOURCE: `001-Fianco sinistro.tcn`]

## 6. Zp — IMPORTANTE

Zp è la quota di profondità richiesta dalla lavorazione lama. La convenzione
geometrica esatta di Zp della lavorazione Busellato deve essere considerata
parte della configurazione macchina. Non va assimilata senza prova alla Z
verticale di una generica geometria orientata TPA.

Il caso verificato ha DS=25 mm, Beta=51,97° e Zp=-31,74 mm. Il valore non è la
semplice negazione dello spessore. Numericamente, la sua componente verticale
secondo la convenzione validata LAME/W95 corrisponde a 25 mm perché
`31,74 × sin(51,97°) ≈ 25`. Questa è una relazione osservata e verificata per la
configurazione corrente, non una definizione universale TPA. [DERIVED:
`010-Divisorio verticale.tcn` + contratto LAME/W95 validato sulla CNC]

Quando cambia spessore, Beta, utensile o configurazione, usare soltanto valori
prodotti da una procedura già verificata e controllare il risultato grafico e
la simulazione macchina.

## 7. Correzione Sx / Dx

La correzione sceglie da quale lato della traiettoria nominale viene collocato
il taglio utile: **Off**, **Sx** o **Dx**. Sinistra e destra si leggono seguendo
il verso da Ps verso Pe; invertire A cambia quindi il loro significato fisico.
[SOURCE: `DBWCUST.WCAD`, campo Correzione; SOURCE: `Workings_ita.pdf`, pp. PDF
44–48, correzione Off/Left/Right]

I due fianchi reali usano lo stesso A=90° ma correzioni opposte: il fianco
sinistro usa Sx (#8525=1), il destro Dx (#8525=2). Questo conferma che la scelta
serve a mantenere il lato finito corretto sui lati opposti. [DERIVED:
`001-Fianco sinistro.tcn` + `002-Fianco destro.tcn`]

Regola operativa: individuare prima lato finito e scarto, poi seguire la freccia
Ps→Pe e scegliere Sx o Dx affinché lo spessore lama resti nello scarto. Verificare
sempre nell'anteprima quale lato occupa il disco. Non correggere un errore di
verso cambiando Sx/Dx senza ricontrollare Ps, A e U.

## 8. Calcola corda

Con una lama inclinata, l'opzione chiede alla lavorazione di calcolare il
posizionamento rispetto alla corda all'ingresso nel pezzo. [SOURCE:
`Workings_ita.pdf`, pp. PDF 44–48, campo Chord/Corda]

Nei tagli esterni rettilinei del corpus Busellato è sempre **Off** (#8527=0),
compresi i casi a Beta 45°, 51,97°, 53,7°, 60,46° e 71,9°. Lasciarla Off quando
si riproducono questi cicli. Attivarla soltanto per una lavorazione per la quale
esista una procedura Busellato specifica già verificata. [SOURCE: corpus
`ValidTCN Programs`, lavorazioni W#1052; SOURCE: `CustomBusellatoBlade/Data`,
lavorazioni W#1052]

## 9. Z2

**Abilita Z2** rende disponibile una seconda quota e la relativa velocità. La
famiglia LAMA documenta un'esecuzione a doppia profondità e il campo FZ per la
passata associata. [SOURCE: `Workings_ita.pdf`, pp. PDF 45, 47–48]

Sulla configurazione Busellato validata, il ciclo `Zp=-3`, Z2 abilitata e
`Z2=-28,07` esegue prima la passata superficiale e poi quella completa. Il
programma del fianco destro usa lo stesso schema con Z2=-29,88. [SOURCE:
`001-Fianco sinistro.tcn`; SOURCE: `002-Fianco destro.tcn`; DERIVED: programmi
reali + validazione CNC del progetto]

Se Velocità Z2 non è compilata, i campioni non permettono di dedurre il valore
effettivo: dipende dalla tecnologia configurata.

## 10. Procedura — squadratura semplice

1. Aprire il pannello e controllare DL, DH, DS, unità e SIDE.
2. Inserire **LAMATA X** per un lato parallelo a X oppure **LAMATA Y** per un
   lato parallelo a Y.
3. Per LAMATA X compilare X iniziale, X finale e Qy; per LAMATA Y compilare Y
   iniziale, Y finale e Qx. [SOURCE: `DBWCUST.WCAD`, BLADEX/BLADEY]
4. Inserire Zp secondo il ciclo macchina approvato.
5. Selezionare macchina, gruppo e utensile lama corretti; controllare le
   velocità proposte dalla tecnologia.
6. Lasciare Calcola corda Off per i tagli rettilinei qui documentati.
7. Scegliere Sx o Dx guardando il verso del taglio e mettendo il disco nel lato
   di scarto.
8. Abilitare Z2 soltanto se è prevista una passata doppia verificata; compilare
   Z2 e l'eventuale Velocità Z2.
9. Controllare graficamente che la corsa superi completamente il materiale e
   che il lato finito resti alla quota richiesta.
10. Compilare il programma, risolvere gli errori e svolgere i normali controlli
    di simulazione e attrezzaggio della macchina.

## 11. Procedura — taglio inclinato BLADEXY

Esempio reale, da usare come controllo di lettura dei campi e non come ricetta
per altri pannelli: [SOURCE: `010-Divisorio verticale.tcn`, “taglio con lama30”;
SOURCE: screenshot `sx.png`, finestra BLADEXY — LAMATA XY]

1. Inserire **LAMATA XY (BLADEXY)** su SIDE1.
2. Impostare X iniziale **1185,04** e Y iniziale **870**.
3. Impostare Zp **-31,74**.
4. Impostare A **270°**: la corsa procede verso Y decrescente.
5. Impostare U **870 mm**.
6. Selezionare utensile **9600**.
7. Impostare Beta **51,97°**.
8. Lasciare Calcola corda **Off**.
9. Impostare Correzione **Sx**.
10. Lasciare Abilita Z2 **Off**.
11. Verificare che Ps→Pe copra il bordo richiesto, che il disco sia sul lato di
    scarto e che l'inclinazione coincida con la faccia finita.

Il record TCN associa questi campi a #8510, #8511, #8512, #8519, #8520, #8516,
#8521, #8527, #8525 e #8526. Questa correlazione serve alla diagnosi; non implica
significati per altri parametri #85xx. [SOURCE: `010-Divisorio verticale.tcn`,
W#1052]

## 12. Procedura — passata Z2

Esempio reale del fianco sinistro: [SOURCE: `001-Fianco sinistro.tcn`, “taglio
di testa”; SOURCE: screenshot `sx.png`, BLADEXY — LAMATA XY]

1. Inserire BLADEXY su SIDE1.
2. Compilare X **1,15**, Y **-180**, Zp **-3**.
3. Impostare A **90°** e U **1140**; la corsa copre DH=780 e si prolunga oltre
   entrambe le estremità.
4. Selezionare utensile **9600** e Beta **60,46°**.
5. Impostare la correzione prevista per il lato finito; in questo campione è
   **Sx**.
6. Lasciare Calcola corda **Off**.
7. Attivare **Abilita Z2** e inserire Z2 **-28,07**.
8. Controllare la velocità Z2 proposta dalla tecnologia: il TCN campione non ne
   memorizza una esplicita.
9. In anteprima e simulazione controllare entrambe le passate, la penetrazione
   finale, il lato del disco e la copertura completa del grezzo.

## 13. Diagnostica

**127 — Programmazione parametrica: radice quadrata di valore negativo [8900]**
e **125 — Programmazione parametrica: divisione per zero [8901]** sono stati
osservati quando la grafica della lamata non riusciva a ottenere geometria o
parametri tecnologici validi dell'utensile. Questo è un caso accertato, non la
causa universale dei due codici. [DERIVED: errori osservati + analisi della copia
di supporto `GRAPHICLAMATA.TMCR`; l'identità con la macro PPC attiva non è stata
dimostrata]

Controllare in quest'ordine:

1. utensile selezionato e sua presenza nella tabella della macchina;
2. diametro, spessore e dati tecnologici necessari;
3. macchina, gruppo ed elettromandrino associati;
4. Beta, Zp, U e altri valori nulli o geometricamente impossibili;
5. compilazione del programma nell'ambiente PPC corretto.

La tabella reale dell'utensile 3000 mostra diametro 300 mm, penetrazione massima
85 mm e spessore 3,2 mm; mostra inoltre propri limiti di regime e avanzamento.
Questi dati appartengono alla tecnologia CNC. [SOURCE: screenshot CNC, “lama 300
GENERICA”, ID utensile 3000]

## 14. Cosa non fare

- Non indovinare il segno di Beta.
- Non cambiare A di 180° supponendo che il risultato sia equivalente.
- Non cambiare Sx/Dx solo per rendere plausibile il disegno: verificare lato
  finito, scarto e verso Ps→Pe.
- Non calcolare Zp dal solo spessore con una formula generica non verificata.
- Non presumere che un ID di catalogo utensili e l'identificatore tecnologico
  nel TCN siano sempre la stessa cosa.
- Non copiare nella lavorazione Busellato il significato interno dei parametri
  di una macro MCR generica o di una copia non identificata.
- Non eseguire il programma soltanto perché il disegno nell'editor appare
  plausibile.

## 15. Scheda rapida

| Voce | Definizione verificata |
|---|---|
| **BLADEX / LAMATA X** | Taglio rettilineo parallelo a X: X iniziale/finale e quota Qy. |
| **BLADEY / LAMATA Y** | Taglio rettilineo parallelo a Y: Y iniziale/finale e quota Qx. |
| **BLADEXY / LAMATA XY** | Taglio da un punto XY lungo direzione A e distanza U, con possibile inclinazione Beta. |
| **A** | Direzione della corsa nel piano XY; 90° va verso +Y, 270° verso -Y nei riferimenti esaminati. |
| **U** | Lunghezza programmata della corsa. |
| **Beta** | Inclinazione/brandeggio della lama; segno e riferimento sono configurazione macchina. |
| **Zp** | Quota di profondità secondo il riferimento Busellato LAME/W95; non è una semplice Z verticale. |
| **Correzione** | Off/Sx/Dx rispetto al verso Ps→Pe; scegliere il lato che lascia il disco nello scarto. |
| **Z2** | Seconda quota, usata nel ciclo verificato dopo la passata superficiale Zp. |
| **Utensile** | ID tecnologico della lama configurata; verificare disponibilità, macchina e gruppo. |
| **Corda** | Riferimento alla corda d'ingresso per lama inclinata; Off in tutti i tagli reali qui esaminati. |

[SOURCE: `DBWCUST.WCAD`, BLADEX/BLADEY/BLADEXY; SOURCE: `Workings_ita.pdf`,
pagine PDF 44–48; SOURCE: `001-Fianco sinistro.tcn`, `002-Fianco destro.tcn`,
`010-Divisorio verticale.tcn`, `011-Divisorio verticale.tcn`; SOURCE: corpus
`ValidTCN Programs`, lavorazioni W#1052]
