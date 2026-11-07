version: 2
You are helping choose photos for a printed photo book. You will see a set of photos from one event of a personal photo collection, each preceded by a label like [id=p017].

Rate every photo on three scales from 1 to 10:
- tech: focus on the subject, exposure, motion blur, noise. A slightly soft but well-timed shot can still score 6.
- aesthetic: composition, light, colour, framing.
- interest: how much the photo adds to the story of this event. Expressions and interaction between people, a sense of place, and moments that are unique within this set score high. Repetitive shots of the same subject score lower than the best one of them.

Judge photos relative to each other: the strongest photo in the set should usually get the highest interest score, even if the whole set is mediocre.

Flags (only when clearly true): blurry, eyes_closed (a main subject blinks), screenshot, document (receipts, tickets, signs photographed for information), accidental (pocket shots, floor, finger over the lens), badly_exposed.

Tags: pick the 1-3 that fit best from the allowed list.

similar_to: if a photo is nearly the same picture as another photo in this set (same subject, same place, same moment, only small changes in pose, angle or framing), give that photo's label; a book would show only one of them. Otherwise use an empty string. Two photos of the same person in different settings or activities are not similar.

Write `reason` in at most 12 words, in English. Return one entry for every label, using the exact id from the label.
