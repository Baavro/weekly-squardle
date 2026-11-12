"""Rebuild backend/words.txt (accepted guesses) and backend/answers.txt (puzzle vocabulary).

Not needed at runtime. Requires: pip install wordfreq
Input: a plain five-letter English word list, one word per line.
Answers are the subset of accepted words that are common (Zipf frequency >= 3.0)
minus proper nouns, brands and vulgar words listed in BLOCK below.

Usage: python tools/build_wordlists.py path/to/wordlist.txt
"""
import sys
from pathlib import Path

from wordfreq import zipf_frequency

BLOCK = set("""
supra felix cowan moran louie virgo nelly pepsi danny oscar scots congo terra welch santo india doris draco swede
coney paris teddy denis erica koran bible allen guido pratt assam roche cohen jones quran shawn mecca capri platt
devon porte calif patsy logan mitch ollie jesus diane monde diana femme tyler greek kyrie craig avant della james
hades accra dolce fanny mayan moira jesse fritz polly corey genoa facto pluto lowry judas argus rubin milly bantu
lotta texas matty sudan moses ramon brits venus monty roman louis barca mahal intra potts sabha outta donny bubba
gimme mandy haute celeb nigga kinda vegas niger vitro kiddo bayer magna fella sluts yates waldo intel momma garde
turks haram homie nudes lakhs annum leone fedex telly faire bello riggs spain shiva rapes raped nazis cunts fucks
cocks dicks boobs farts shits porno busty nagar forza putin linux forex lemme shite tesla hicks colby kirby hanks
biggs benji terre johns starr sykes coles jolie clair margo foley rubio lacey darcy verde versa barre sorta hella
comms noone heres asses butts sucks whore bitch slut dildo penis vagina pussy twats twat wanks wanker
tania lohan titty pubic fecal banda hurst britt macon amman reddy ville derry conte salle firth marge parma liang
prius bafta actin massa croft scala thong sahib pasha playa banco carte masse gabby aunty alamo tonga savoy brill
xerox taser spank hertz
abbas allan ariel barry benny betty billy bobby bowie brent brock bundy burke buffy chang chico ching clint colin
costa dixie donna dover drake emery garth gemma hogan harry henry honda japan jenny jerry jimmy jihad julia kelly
kerry kraft kylie laura levin lewis chevy cisco fitch gypsy boner horny kinky booty dunno gonna gotta china chile
dutch malik manny maria missy molly monte nancy nicky norma paolo patel paddy pedro peggy perry peter petit ralph
randy riley rohan roger rufus rowan sally sammy silva sloan slade smith sonny tammy terry tommy wally weber wigan
wight willy welsh wales swiss yahoo whats wanna takin carol lynch merch queer sperm moron sissy prima merle anglo
nigra negro dykes dyke fagot faggy retard spick chink gooks kikes pakis tranny rapist semen
""".split())


def main(src: str, out: Path = Path(__file__).resolve().parent.parent / "backend") -> None:
    with open(src, encoding="utf-8") as f:
        words = sorted({w.strip().lower() for w in f if len(w.strip()) == 5 and w.strip().isalpha()})
    answers = [w for w in words if zipf_frequency(w, "en") >= 3.0 and w not in BLOCK]
    (out / "words.txt").write_text("\n".join(words) + "\n", encoding="utf-8")
    (out / "answers.txt").write_text("\n".join(answers) + "\n", encoding="utf-8")
    print(len(words), "accepted guesses;", len(answers), "answers")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1])
