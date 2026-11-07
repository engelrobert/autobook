version: 1
A person wants a photo book made from a folder of photos. They may have marked their favourite photos in the folder or file names. You get the marks a program detected (folder words, characters at the start or end of file names) with photo counts, how many of them overlap with another mark, examples, and the list of all folder names.

For each detected mark, decide whether it most likely marks favourites (is_favourite) and give a level: 1 = a favourite, 2 = one of the very best (for example a "+" inside a favourites folder, or a stronger version of another mark). For "+" at the start of file names, level is per plus sign: "++" counts twice automatically, so give the level of a single "+".

Typical favourite marks: folders called Favoriten, Favorites, beste, Auswahl, Highlights, für das Fotobuch; file names starting with "+" or "!". Not favourite marks: camera or app names and copies (DCIM, Camera, PORTRAIT.ORIGINAL, "(1)", "_DSC", "-edited"), a folder of screenshots, or a word that is part of a place name (for example "Top of the Rock").

Write `reason` in at most 15 words, in English. Return one entry per detected mark with its exact kind and pattern.
