"""Real OCR output, kept verbatim.

Both of these are the same photograph: a handwritten crepe recipe on a lined
index card, shot at a slight angle. They are what the quality gate's thresholds
are actually tuned against, so they live here rather than as invented strings --
a made-up "bad OCR" sample proves whatever its author assumed.
"""

# Apple Vision, accurate recognition level. Imperfect but reconstructible: the
# quantities and every verb survive.
CREPE_CARD_APPLE_VISION = """Crepe
/ cup flour
/ tsp suçau
1/4 tsp sacr
/ top Vaneer
3 eggs
2 cups er
2 Has butter melted
Beat eggs t me togern i blende ou nuxer.
Add flowmete te smorte, Stu i netted butter
+ Varila
Heat par a wipe with oiled papa, cle.
i parishes
Us appux 2= tos por end cupe. Tyr
rosemarys
lotate fu to ger en.. Bourn bote see.
de treme
Soups • Salads
Appetizers •
Main Dishes"""

# rapidocr on the identical image. A printed-text engine on cursive: words run
# together, whole lines are lost. Nothing downstream could use this.
CREPE_CARD_RAPIDOCR = """drepe
56688
1cupblouR
1tspsugau
Hhsbuuenmelten
sds
1tsp Vaniele
ino
Beateggsalgrubleoe
Gddflowmyhutulcottr)tneltdbutte
tvavieia
Heatpanewyeewutldpaclit
parsleyp
UapRia Hhs poou cuuT4
sage
Bioura brt4u
Lotatepuhureh.
rosemary
Serreguingans Ncotarce
&ethapne"""
