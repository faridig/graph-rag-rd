# FICHE TECHNIQUE CIR — OPÉRATION DE R&D

| Champ | Valeur |
|---|---|
| **Groupement** | Nouvelles voies de texturation des protéines végétales |
| **Sous-opération** | Évaluation du procédé DST (Direct Shear Technology / Sheartex) comme alternative à l'HME pour la texturation des protéines végétales |
| **Période** | 2024-12-12 → 2025-06-05 |
| **Leads** | Emilie Chevrier, Maxime Demême |
| **Nombre d'essais** | 6 (DST-1, DST-2, DST-3, DST-4, DST-5, DST-10) |

---

## 1. VERROU SCIENTIFIQUE

### 1.1 Contexte technologique

ACCRO produit des analogues de viande fibrés par extrusion haute humidité (High Moisture Extrusion, HME) sur extrudeur bi-vis EV32. Ce procédé impose un couplage fort entre les variables thermiques (profil de températures par zones Z1–Z10), mécaniques (vitesse de vis, SME) et rhéologiques (composition protéique, taux d'humidité), rendant difficile l'optimisation indépendante de chaque paramètre.

Dans le cadre de l'installation d'une ligne industrielle d'émincés, la question de la technologie de texturation a été ouverte. Le procédé DST (Direct Shear Technology, équipement Sheartex, fournisseur Sobatech) constitue une voie alternative : il découple le temps de séjour thermique (mixeur, ~20 min) du temps de séjour mécanique (filière, ~20 min), et permet un nettoyage en place (CIP) sans démontage. Il présente également une potentielle flexibilité fournisseur et un format de sortie en pièce entière non obtenu par HME standard.

### 1.2 Nature du verrou

**Aucune donnée scientifique ou industrielle n'était disponible, au démarrage de l'opération, sur le comportement du procédé DST appliqué aux recettes et ingrédients spécifiques utilisés par ACCRO.**

Les inconnues initiales portaient sur plusieurs dimensions interdépendantes :

- **Transposabilité des recettes ACCRO au procédé DST** : les formulations M03 (Milanaise, base soja/gluten) et P01 (Poulet allumettes, base pois/gluten) ont été développées pour l'HME. Leur rhéologie, leur comportement sous cisaillement lent à haute température, et leur aptitude à former une bande fibrée en filière longue (9 m en DST-1, 250 cm en DST-3 à DST-5) étaient totalement inconnus.

- **Sensibilité des matrices protéiques au mode de structuration DST** : la fibration en HME résulte d'un cisaillement élevé en bi-vis à haute SME. En DST, la structuration repose sur un cisaillement lent contrôlé en filière longue à basse pression différentielle. L'effet de ces conditions sur l'anisotropie structurale des matrices ACCRO n'avait pas été établi.

- **Compatibilité des inclusions TVP avec le procédé DST** : l'injection de TVP (Textured Vegetable Protein) en cours de process DST à haute température (~140 °C) était envisagée pour produire des textures de type effiloché. Le comportement thermomécanique des TVP commerciales (blé, fèves) sous ces conditions était inconnu.

- **Identification des paramètres process déterminants** : débit, fréquence de cisaillement, profil thermique (Z1/Z2), longueur de filière, taux d'eau, taux d'huile — l'influence respective et les interactions de ces variables sur la fibration (mesurée par l'indice d'anisotropie AI = Cut_T / Cut_L, cible > 1,2) n'avaient pas été caractérisées sur les recettes ACCRO.

Ce verrou constitue un **obstacle scientifique et technique non résolu par la littérature existante** : la technologie DST/Sheartex est récente, peu documentée scientifiquement (relevé explicitement dans l'analyse SWOT de l'essai DST-1 : « peu de recul scientifique »), et aucun référentiel de transposition depuis des recettes HME vers le DST n'était disponible pour les matrices pois/gluten et soja/gluten utilisées par ACCRO [source: DST-1].

---

## 2. DÉMARCHE EXPÉRIMENTALE

La démarche a suivi une logique exploratoire progressive, organisée en cinq temps : découverte du procédé, comparaison inter-procédés, exploration géométrique, optimisation paramétrique, et mécanistique.

### 2.1 Phase 1 — Essais de découverte DST sur recettes M03 et P01 (2024-12-12)

**Essai DST-1** constitue le premier contact du laboratoire ACCRO avec la technologie DST. Deux journées d'essai ont été conduites sur pilote DST (filière 9 m, section 2,5 cm × 12 cm, mixeur 18 L) :

- **Jour 1** : formulation M03 à base d'isolat de soja (Unisol GP, 20,5 %), gluten de blé vital (Vital Viten, 13,8 %), eau (59 %), huile (5 %), amidon (1,7 %). SME mesurée : 120 Wh/kg. Débit : 150 kg/h. Indice d'anisotropie mesuré : AI = 1,865.
- **Jour 2** : formulation P01 à base de protéine de pois (Greenboy). Deux conditions de température mixeur testées : 115 °C et 120 °C. Indice d'anisotropie : AI = 2,668 (115 °C) et AI = 3,232 (120 °C). Pour la condition P01 DST 60 % H₂O : AI calculé = 2,257.

L'objectif était d'établir si le procédé DST pouvait physiquement produire une bande fibrée à partir des matrices ACCRO, sans préjuger de la qualité du produit final. [source: DST-1]

### 2.2 Phase 2 — Comparaison inter-procédés et inter-ingrédients sur HME (2025-01-08)

**Essai DST-2** a été conduit sur l'extrudeur bi-vis EV32 (HME) afin d'établir une base de comparaison entre les ingrédients protéiques candidats, dans les conditions procédé maîtrisées par ACCRO. Trois formulations ont été testées à débit constant (30 kg/h) :

- Essai 1 (référence) : Nutralys F85M, profil thermique standard → AI = 1,083
- Essai 2 (référence, profil thermique optimisé, zones Z7–Z10 rehaussées) : Nutralys F85M → AI = 1,354
- Essai 3 : isolat de pois Greenboy, profil thermique standard → AI = 1,478

Cet essai a permis de dissocier l'effet de l'ingrédient (F85M vs Greenboy) de l'effet du profil thermique sur la fibration en HME, constituant une base de référence pour interpréter les essais DST ultérieurs. [source: DST-2]

### 2.3 Phase 3 — Exploration géométrique de filière et compatibilité process sur Sheartex (2025-01-28)

**Essai DST-3** a été conduit sur l'équipement Sheartex (Sobatech) avec filière modulaire de 250 cm. Deux produits ont été testés :

- **M03** (3 runs, 18 à 22,4 kg/h, Tmat2 125–137 °C, filière complète 250 cm) : exploration de la plage de débit et de température.
- **P01 standard** (run 4) : viscosité excessive dès le démarrage (30 bar avec filière 250 cm) → réduction à 150 cm (3 blocs sur 5). Résultat : exsudation d'huile, non viable.
- **P01 60 % eau** (run 5) : variante formulaire testée comme voie de contournement à 12,5 kg/h, temps de séjour estimé ~12,82 min. Aucune évaluation sensorielle enregistrée.

La découverte de l'incompatibilité de la formulation P01 standard avec la filière Sheartex à longueur complète constitue un résultat scientifique majeur de cette phase. [source: DST-3]

### 2.4 Phase 4 — Optimisation paramétrique et modifications recette P01 sur DST (2025-02-12 et 2025-02-25)

**Essai DST-4** (deux journées) a exploré de façon systématique les leviers process et recette sur la formulation P01 :

- Jour 1 : variation de débit (influence du throughput sur l'anisotropie), température Z1 (odeur torréfiée à Z1 ~111 °C, correction à 104–106 °C), fréquence de cisaillement (Hz), pression filière (seuil minimal ~7 bar identifié).
- Jour 2 : variation du taux d'eau (60 % H₂O, Recette 1) et du taux d'huile (~3 %, Recette 2 ; ~4,86 % envisagé ultérieurement), avec suivi de l'anisotropie instrumentale sur 5 points de process.

[source: DST-4]

**Essai DST-5** (deux journées, site Sobatech) a poursuivi l'optimisation P01 avec deux géométries de filière (petite et grande), un triplement du taux d'huile (4,86 %) et un test d'incorporation d'arôme liquide (1,38 %) dans la pâte. L'objectif était de définir les paramètres idéaux pour le dimensionnement de la machine industrielle. [source: DST-5]

### 2.5 Phase 5 — Étude mécanistique du comportement des TVP sous cisaillement et cuisson (2025-06-05)

**Essai DST-10** a utilisé le Rapid Visco Analyser (RVA) comme outil de laboratoire pour reproduire indépendamment les deux sollicitations du procédé DST — cisaillement (960 rpm, 10 min) et cuisson (palier 140 °C) — et identifier le facteur responsable de la perte de structure des TVP observée sur site. Quatre types de TVP ont été testés : TVP blé UNITEX, TVP blé Loryma (chunks), TVP fèves, et TVP blé Loryma (effiloché, non documenté). [source: DST-10]

---

## 3. RÉSULTATS (incluant essais non concluants)

### 3.1 Faisabilité de la texturation DST sur formulations ACCRO

#### 3.1.1 Formulation M03 (soja/gluten puis pois/gluten)

Le procédé DST permet d'obtenir des bandes fibrées avec des indices d'anisotropie supérieurs à la cible de 1,2 pour la formulation M03 dès les premiers essais exploratoires [source: DST-1] :

| Condition | AI mesuré | Cible |
|---|---|---|
| M03 soja, Jour 1 DST (pilote 9 m) | 1,865 | > 1,2 |
| M03 pois, 115 °C mixeur | 2,668 | > 1,2 |
| M03 pois, 120 °C mixeur | **3,232** | > 1,2 |
| M03 pois DST 60 % H₂O | 2,257 | > 1,2 |

Sur la filière Sheartex (250 cm), la formulation M03 est processable sur la plage 18–22,4 kg/h, Tmat2 125–137 °C. Une qualité sensorielle acceptable est obtenue à partir de Tmat2 ≥ 135 °C (runs 2 et 3). En dessous (run 1, 18 kg/h) : produit collant, pâteux, astringent [source: DST-3].

#### 3.1.2 Formulation P01 (pois/gluten) — résultats et difficultés

La formulation P01 standard s'est révélée **incompatible avec la filière Sheartex à longueur complète** : viscosité excessive entraînant des pressions > 30 bar dès le démarrage, exsudation d'huile en surface, non-structuration [source: DST-3]. Cette limite prouve que le comportement rhéologique des matrices protéiques dans le procédé DST ne peut pas être prédit directement depuis les résultats HME.

Les essais DST-4 et DST-5 ont permis d'identifier des conditions permettant de dépasser la cible AI > 1,2, mais avec une reproductibilité limitée et des compromis sensoriels importants :

| Run | Condition | AI mesuré | Observation sensorielle |
|---|---|---|---|
| J1-11h42 | Recette base, débit faible | 1,179 | **En dessous de la cible** — texture pâteuse |
| J1-16h20 | Débit augmenté (>20 kg/h) | 1,430 | Amélioration fibration |
| J2-14h28 | Recette 2 (3 % huile), shear 60 Hz max | **1,654** | Produit fibré, élastique, ferme |
| J1-Essai2 (petite filière, 48 Hz, F1) | DST-5 | 1,077 | **En dessous de la cible** |
| J1-17h09 (grande filière, 35 Hz, F1) | DST-5 | 1,059 | **En dessous de la cible** |
| J1-17h33 (grande filière, refroid. coupé) | DST-5 | 1,108 | **En dessous de la cible** |
| J2-10h29 (petite filière, 60 Hz, F2) | DST-5 | **1,416** | Goût brûlé fort |
| J2-14h18 (petite filière, 50 Hz, F3 arôme) | DST-5 | 1,209 | Marginalement au-dessus |

[sources: DST-4, DST-5]

**Limite identifiée — DST-5** : dans la majorité des conditions testées lors de DST-5, la fibration P01 reste en dessous de la cible (AI < 1,2). Le seul point nettement au-dessus (J2-10h29, AI = 1,416) est associé à un goût de brûlé fort, rendant le produit inacceptable organoleptiquement. L'incorporation d'arôme à 1,38 % (F3) génère des instabilités de débit (effet de pompage, « cratching ») et une perte d'élasticité de la matière. L'augmentation du taux d'huile à 4,86 % produit une matière plus molle avec sortie froide (~60 °C), incompatible avec une expansion ou une structuration correcte [source: DST-5]. Ces difficultés répétées prouvent l'**étendue de l'incertitude** sur les interactions formulation/procédé DST pour la matrice P01.

#### 3.1.3 Référence HME — comparaison inter-ingrédients

Sur EV32 (HME), l'isolat de pois Greenboy produit une anisotropie supérieure à la référence F85M à profil thermique standard (AI = 1,478 vs 1,083). Le rehaussement thermique Z7–Z10 améliore F85M de +25 % (AI = 1,354). La SME Greenboy est légèrement supérieure (61 vs 58 Wh/kg) et les pressions filière nettement plus élevées (+50 % pression filière vs F85M) [source: DST-2]. Ces résultats confirment que le choix de l'ingrédient protéique est un paramètre de premier ordre, indépendamment du procédé.

### 3.2 Paramètres process identifiés comme déterminants

#### 3.2.1 Température matière dans le mixeur DST

La température matière dans le mixeur est identifiée comme **levier clé** de la fibration dès DST-1 : la condition 120 °C (AI = 3,232) est nettement supérieure à 115 °C (AI = 2,668) [source: DST-1].

#### 3.2.2 Débit (throughput)

En DST-4, l'augmentation du débit au-delà de 20 kg/h est le paramètre ayant l'impact le plus significatif sur la fibration P01, en réduisant le temps de séjour dans la filière. Le run J1-16h20 (débit élevé) produit AI = 1,430 vs AI = 1,179 pour J1-11h42 (débit faible), soit +21,2 % [source: DST-4]. En DST-3, le débit de M03 augmente naturellement avec la température (18 → 22,4 kg/h) par fluidification de la matrice [source: DST-3].

#### 3.2.3 Taux d'huile

L'augmentation du taux d'huile à ~3 % (Recette 2, DST-4) améliore la fibration (AI max = 1,654), facilite le décollement des feuillets (effet « ficello ») et supporte des températures process plus élevées. En revanche, le passage à 4,86 % d'huile (DST-5) produit une matière trop molle et des sorties froides, dégradant la structuration [sources: DST-4, DST-5].

#### 3.2.4 Fréquence de cisaillement (Hz)

Le cisaillement n'est **pas un levier de fibration** en DST (analogue au constat fait en HME) : il agit principalement sur l'apport thermique via l'huile et l'efficacité énergétique. Une fréquence trop élevée entraîne un goût de brûlé sans amélioration structurale [sources: DST-4, DST-5].

#### 3.2.5 Pression filière

Un seuil minimal de pression filière d'environ 7 bar a été identifié en DST-4 comme condition nécessaire à une fibration correcte [source: DST-4].

#### 3.2.6 Longueur de filière

La réduction de la filière de 250 cm à 150 cm (3 blocs) était indispensable pour la formulation P01 standard en DST-3, mais n'a pas suffi à rendre le produit viable [source: DST-3]. L'exploration petite filière / grande filière en DST-5 montre que la géométrie de filière influence le comportement rhéologique de la matière mais n'est pas suffisante pour lever les verrous de fibration P01 à elle seule [source: DST-5].

### 3.3 Comportement des TVP sous sollicitations DST — essai RVA

Les résultats de DST-10 dissocient pour la première fois les effets du cisaillement et de la cuisson sur la structure des TVP :

- **Cisaillement seul (960 rpm, 10 min, sans montée en température)** : structure filandreuse des TVP blé conservée, aspect globalement intact.
- **Cuisson seule (palier 140 °C)** : coagulation complète pour les TVP blé (UNITEX et Loryma) — formation d'un bloc de pâte compact, perte totale de la structure fibreuse et de l'aspect « viande ».
- **TVP fèves** : coagulation présente mais moins compacte, structure partiellement maintenue après cuisson 140 °C.

[source: DST-10]

**Limite identifiée** : les mesures sont exclusivement qualitatives/visuelles (observation et photos). Aucune mesure instrumentale de texture (TPA) ni de température de dénaturation (DSC, rhéologie) n'a été réalisée. La photo des TVP fèves après cuisson est manquante dans le compte-rendu. Le format effiloché Loryma, mentionné comme existant, n'a pas été testé [source: DST-10]. ⚠ Données partielles.

### 3.4 Synthèse des données non mesurées (limites analytiques récurrentes)

Les essais de cette opération présentent de façon récurrente des données analytiques planifiées mais non réalisées, constituant des **zones d'incertitude documentées** :

| Grandeur | Essais concernés |
|---|---|
| SME (Wh/kg) | DST-2, DST-3, DST-4, DST-5 (non calculable sur équipement DST) |
| Humidité / Matière sèche | DST-1 (partielle), DST-2, DST-4, DST-5 |
| Activité de l'eau (aw) | DST-1, DST-2, DST-4, DST-5 |
| pH | DST-1, DST-2, DST-3, DST-4, DST-5 |
| Colorimétrie L*a*b* | DST-1, DST-2, DST-3, DST-5 |
| Teneur en protéines (Dumas) | DST-1, DST-2, DST-4, DST-5 |
| TPA complet | DST-3 (aucun), DST-10 (aucun) |

⚠ Données partielles — ces lacunes reflètent le caractère exploratoire des essais et la priorité donnée aux mesures d'anisotropie structurale (Cut_T / Cut_L) comme indicateur de fibration.

---

## 4. NOUVELLES CONNAISSANCES ACQUISES

### 4.1 Faisabilité différenciée selon la formulation

L'opération établit que la **faisabilité du procédé DST est fortement dépendante de la formulation** et ne peut être inférée depuis les performances HME. La formulation M03 (soja ou pois/gluten, haute humidité) est compatible avec le DST sur une large plage de conditions, avec des indices d'anisotropie dépassant significativement la cible (AI jusqu'à 3,23 en conditions optimales) [source: DST-1]. La formulation P01 standard est en revanche incompatible avec la filière Sheartex à longueur complète en raison de sa viscosité élevée, et nécessite des modifications formulaires substantielles (taux d'eau, taux d'huile) pour être processable [source: DST-3].

### 4.2 Hiérarchisation des leviers de fibration en procédé DST

L'opération produit une première hiérarchisation des paramètres d'influence sur la fibration (AI) pour les matrices ACCRO en DST :

1. **Température matière dans le mixeur** : paramètre de premier ordre — 120 °C donne AI = 3,23 vs 2,67 à 115 °C pour M03 pois [source: DST-1]
2. **Débit (throughput)** : paramètre déterminant pour P01 — débit > 20 kg/h réduit le temps de séjour et améliore la fibration [source: DST-4]
3. **Taux d'huile** : fenêtre étroite identifiée (~3 % optimum) — en dessous : fibration insuffisante ; au-delà de 4,86 % : matière trop molle [sources: DST-4, DST-5]
4. **Fréquence de cisaillement** : **sans effet significatif sur la fibration** — impact sur l'apport thermique et le goût (brûlé à haute fréquence) [sources: DST-4, DST-5]
5. **Longueur de filière** : influence la viscosité apparente de la matrice et la pression process, mais ne peut à elle seule compenser une inadéquation formulaire [sources: DST-3, DST-5]

### 4.3 Connaissance sur le comportement de l'ingrédient protéique

L'isolat de pois Greenboy produit une fibration supérieure à Nutralys F85M en HME (AI = 1,478 vs 1,354 à profil thermique optimisé), mais génère des pressions filière nettement plus élevées (+50 %), impliquant des contraintes process spécifiques [source: DST-2]. L'utilisation de Nutralys F853M (variante de F85M) en J2 de DST-4 constitue une donnée complémentaire sur la sensibilité formulaire, sans caractérisation complète à ce stade [source: DST-4]. ⚠ Données partielles.

### 4.4 Identification du facteur de dégradation des TVP dans le procédé DST

L'essai DST-10 apporte une connaissance mécanistique nouvelle : **la température, et non le cisaillement, est le facteur déterminant de la dégradation des TVP dans le procédé DST**. À 140 °C, les TVP de blé (UNITEX, Loryma) fondent et forment un bloc homogène, perdant toute structure fibreuse. Cette connaissance explique rétrospectivement les difficultés observées lors des essais d'injection TVP sur site (Vitry). Elle ouvre une piste formulaire documentée : les TVP de fèves résistent partiellement à 140 °C et constituent une voie d'exploration pour des procédés DST à température standard [source: DST-10].

### 4.5 Contraintes process spécifiques à la technologie DST

L'opération documente un ensemble de caractéristiques opératoires de la technologie DST jusqu'alors non caractérisées pour les recettes ACCRO :

- Temps de démarrage/arrêt long (> 40 min), générant une perte matière significative lors des changements de conditions [source: DST-1]
- SME plus élevée que l'HME sur recette M03 soja (120 Wh/kg vs ~58–61 Wh/kg en HME) [source: DST-1 ; source: DST-2]
- Température sortie produit plus basse qu'en HME (moindre évaporation → humidité finale plus élevée → texture plus molle) [source: DST-3]
- Reproductibilité limitée inter-runs du fait de la sensibilité au prémix, au feeding et aux transitions entre recettes [source: DST-4]
- Absence de mesure SME en temps réel sur l'équipement DST pilote (couple moteur non enregistré), constituant une limite instrumentale structurelle pour la caractérisation du procédé [sources: DST-3, DST-4, DST-5]

### 4.6 Synthèse des connaissances et état des incertitudes résiduelles

| Connaissance acquise | Niveau de confiance | Source(s) |
|---|---|---|
| DST compatible avec M03, AI > 1,2 atteint | Confirmé | DST-1, DST-3 |
| Température mixeur = levier n°1 fibration M03 | Confirmé | DST-1 |
| P01 standard incompatible filière Sheartex complète | Confirmé | DST-3 |
| Débit > 20 kg/h améliore fibration P01 | Observé, non généralisé | DST-4 |
| Taux d'huile ~3 % optimum P01 DST | Observé, fenêtre étroite | DST-4, DST-5 |
| Cisaillement (Hz) : pas d'effet sur AI | Observé | DST-4, DST-5 |
| Température = facteur de dégradation TVP (vs cisaillement) | Confirmé qualitativement | DST-10 |
| TVP fèves plus résistantes que TVP blé à 140 °C | Observé qualitativement | DST-10 |
| Profil optimal P01 sur DST industriel | **Non établi** ⚠ | — |
| Fenêtre d'eau / d'huile pour P01 sans brûlé | **Non établi** ⚠ | — |
| Comportement TVP fèves (quantitatif) | **Non établi** ⚠ | — |

---

## SOURCES

| ID run | Date | Titre | Statut | Lien |
|---|---|---|---|---|
| DST-1 | 2024-12-12 | DST-DECOUVERTE-241212 — Essais de découverte DST – procédé Direct Shear Technology, produits soja et pois | Terminé | [Ouvrir](https://nxtfoodfr.sharepoint.com/sites/RD/_layouts/15/Doc.aspx?sourcedoc=%7B43A4617C-E72C-426B-BCB4-1A8B1A72DBE8%7D&file=DST-1%20Essais%20de%20d%C3%A9couverte%20DST.xlsx&action=default&mobileredirect=true) |
| DST-2 | 2025-01-08 | DST-250108 — Essais recettes DST sur EV32 — comparaison isolat de soja, Nutralys F85M et isolat de pois greenboy | Terminé | [Ouvrir](https://nxtfoodfr.sharepoint.com/sites/RD/_layouts/15/Doc.aspx?sourcedoc=%7B67F298EE-6C1D-4962-87EC-936216F44EBF%7D&file=DST-2%20Essais%20recettes%20DST%20sur%20EV32.xlsx&action=default&mobileredirect=true) |
| DST-3 | 2025-01-28 | DST-3 — DST Sheartex Trials on M03 and P01 — Die Geometry and Process Parameter Exploration | Terminé | [Ouvrir](https://nxtfoodfr.sharepoint.com/sites/RD/_layouts/15/Doc.aspx?sourcedoc=%7B4C3A562E-819F-434F-AC8C-5B0F0B95865F%7D&file=DST-3%20-%20DST%20Trials%20on%20P01%20and%20M03.xlsx&action=default&mobileredirect=true) |
| DST-4 | 2025-02-12 | DST-4 — DST Trials on P01 — exploration process (J1) et modifications recette (J2) | Terminé | [Ouvrir](https://nxtfoodfr.sharepoint.com/:x:/r/sites/RD/_layouts/15/Doc.aspx?sourcedoc=%7B9290E795-F268-47F8-BE86-42687BF7C3E5%7D&file=DST-4%20-%20DST%20Trials%20on%20P01.xlsx&action=default&mobileredirect=true) |
| DST-5 | 2025-02-25 | DST-5 — P01 process adjustment and oil aroma addition | Terminé | [Ouvrir](https://nxtfoodfr.sharepoint.com/:x:/r/sites/RD/_layouts/15/Doc.aspx?sourcedoc=%7B97464ABA-8EE5-425D-8001-E43DD8B364C4%7D&file=DST-5%20-%20P01%20process%20adjustment%20and%20oil%20aroma%20addition.xlsx&action=default&mobileredirect=true) |
| DST-10 | 2025-06-05 | DST-10 — TVP au RVA – Étude du comportement de différents types de TVP sous cisaillement et cuisson | Terminé | [Ouvrir](https://nxtfoodfr.sharepoint.com/sites/RD/_layouts/15/Doc.aspx?sourcedoc=%7B8C0F0693-6F5B-46AA-9866-DEEA66C9FB27%7D&file=DST-10%20TVP%20au%20RVA%20CR.docx&action=default&mobileredirect=true) |