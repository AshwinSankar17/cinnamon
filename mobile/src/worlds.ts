/**
 * Cinnamon worlds: one record per place the roll can live (docs/superpowers/specs/2026-10-15-cinnamon-design.md §7.2).
 * Tokens are generated from the approved mockup (docs/design/cinnamon/Cinnamon.dc.html): `day`/`night` UI tokens plus
 * `s*` scene tokens used only by that world's scene. A world is data plus one scene component.
 */
import { AlegreyaSans_400Regular } from '@expo-google-fonts/alegreya-sans/400Regular';
import { AlegreyaSans_700Bold } from '@expo-google-fonts/alegreya-sans/700Bold';
import { Alegreya_800ExtraBold } from '@expo-google-fonts/alegreya/800ExtraBold';
import { Caveat_600SemiBold } from '@expo-google-fonts/caveat/600SemiBold';
import { CourierPrime_400Regular } from '@expo-google-fonts/courier-prime/400Regular';
import { DMMono_400Regular } from '@expo-google-fonts/dm-mono/400Regular';
import { Figtree_400Regular } from '@expo-google-fonts/figtree/400Regular';
import { Figtree_600SemiBold } from '@expo-google-fonts/figtree/600SemiBold';
import { Fraunces_600SemiBold } from '@expo-google-fonts/fraunces/600SemiBold';
import { Fredoka_600SemiBold } from '@expo-google-fonts/fredoka/600SemiBold';
import { HankenGrotesk_400Regular } from '@expo-google-fonts/hanken-grotesk/400Regular';
import { HankenGrotesk_600SemiBold } from '@expo-google-fonts/hanken-grotesk/600SemiBold';
import { InstrumentSans_400Regular } from '@expo-google-fonts/instrument-sans/400Regular';
import { InstrumentSans_600SemiBold } from '@expo-google-fonts/instrument-sans/600SemiBold';
import { MartianMono_400Regular } from '@expo-google-fonts/martian-mono/400Regular';
import { Sora_400Regular } from '@expo-google-fonts/sora/400Regular';
import { Sora_600SemiBold } from '@expo-google-fonts/sora/600SemiBold';
import { Unbounded_500Medium } from '@expo-google-fonts/unbounded/500Medium';
import { YoungSerif_400Regular } from '@expo-google-fonts/young-serif/400Regular';

export const WORLD_IDS = ['bakery', 'forest', 'rustic', 'space', 'tide'] as const;
export type WorldId = (typeof WORLD_IDS)[number];

export type Tokens = { ink: string; ink2: string; ink3: string; raise: string; bg2: string; line: string; accent: string; needs: string; finding: string } & Record<string, string>;

export type World = {
  id: WorldId; name: string; where: string; line: string; need: string;
  presenter: 'tickets' | 'tags' | 'cards' | 'satellites' | 'bubbles'; accessory: 'icing' | 'leaf' | 'helmet' | null;
  pet: { name: string; kind: string }; displayScale: number;
  fonts: { display: string; ui: string; uiSemi: string; mono: string; hand: string };
  fontFiles: Record<string, number>; day: Tokens; night: Tokens;
};

export const WORLDS: Record<WorldId, World> = {
  bakery: {
    id: 'bakery', name: "Bakery", where: "Fresh out of the oven", need: "ORDER UP",
    line: "The roll cools on a rack behind the counter. Threads are order tickets on the rail; what needs you is the ticket that says Order up.",
    presenter: 'tickets', accessory: 'icing', pet: { name: "Crumb", kind: "sparrow" }, displayScale: 1.0,
    fonts: {"display": "Fraunces_600SemiBold", "ui": "InstrumentSans_400Regular", "uiSemi": "InstrumentSans_600SemiBold", "mono": "DMMono_400Regular", "hand": "Fraunces_600SemiBold"},
    fontFiles: { Fraunces_600SemiBold, InstrumentSans_400Regular, InstrumentSans_600SemiBold, DMMono_400Regular },
    day: {"ink": "#2A1A10", "ink2": "#5E4636", "ink3": "#7D6756", "raise": "#FFFBF5", "bg2": "#F3E6D4", "line": "rgba(42,26,16,.12)", "accent": "#B4561F", "needs": "#E8A22C", "finding": "#4B6332", "sWall": "#F7E7D0", "sTile": "#F3DEC2", "sTileLine": "#E7CBA6", "sFrame": "#FFFFFF", "sSky1": "#BFE0F0", "sSky2": "#F8EBD3", "sOrb": "#FFE3A0", "sRoofs": "#D9BC97", "sWood": "#B9784A", "sWoodDark": "#8E5530", "sTop": "#D49B65", "sRack": "#6B4A35", "sRail": "#A3A7AB", "sGlow": "rgba(255,214,150,.55)", "sGlass": "rgba(255,255,255,.6)", "sPaper": "#FFFDF8"},
    night: {"ink": "#F6E8D6", "ink2": "#D0BBA5", "ink3": "#AA937E", "raise": "#2C2019", "bg2": "#3A2B21", "line": "rgba(246,232,214,.13)", "accent": "#F0A35E", "needs": "#F2B04A", "finding": "#A9C27A", "sWall": "#3A2A20", "sTile": "#3F2E24", "sTileLine": "#4C382B", "sFrame": "#5A4334", "sSky1": "#101B33", "sSky2": "#2A3858", "sOrb": "#F4EBD0", "sRoofs": "#0B1222", "sWood": "#5E3B22", "sWoodDark": "#3E2616", "sTop": "#7A4F2E", "sRack": "#2A1D15", "sRail": "#6E6A66", "sGlow": "rgba(255,170,90,.38)", "sGlass": "rgba(255,240,220,.2)", "sPaper": "#F2E6D3"},
  },
  forest: {
    id: 'forest', name: "Forest", where: "Under the old oak", need: "A NOTE FOR YOU",
    line: "The roll sits on a stump under a great oak. Threads hang as tags from its branch; what needs you is a note hung up for you.",
    presenter: 'tags', accessory: 'leaf', pet: { name: "Hazel", kind: "squirrel" }, displayScale: 0.96,
    fonts: {"display": "YoungSerif_400Regular", "ui": "HankenGrotesk_400Regular", "uiSemi": "HankenGrotesk_600SemiBold", "mono": "IBMPlexMono_400Regular", "hand": "YoungSerif_400Regular"},
    fontFiles: { YoungSerif_400Regular, HankenGrotesk_400Regular, HankenGrotesk_600SemiBold },
    day: {"ink": "#1C2619", "ink2": "#44523D", "ink3": "#5E6B57", "raise": "#F8F9F1", "bg2": "#E6EAD8", "line": "rgba(28,38,25,.13)", "accent": "#3F6B2E", "needs": "#D9A22C", "finding": "#3F6B2E", "sSky1": "#CFE5D6", "sSky2": "#F4F1DC", "sOrb": "#FFF1C4", "sFar": "#B7CFA8", "sPines": "#9EBC8F", "sMid": "#8DB377", "sGround": "#6E9C55", "sGrass": "#5A8746", "sTrunk": "#6E4B32", "sBark": "#563A26", "sLeaf1": "#3F6E36", "sLeaf2": "#4F8540", "sLeaf3": "#68A250", "sTag": "#E2BC86", "sTagLine": "#8C6A44", "sStump": "#7B5638", "sStumpTop": "#D2A971", "sRing": "#9D7445", "sRay": "rgba(255,255,236,.38)", "sCap": "#D9573C"},
    night: {"ink": "#E6EBDD", "ink2": "#B3C0AA", "ink3": "#909D89", "raise": "#18241B", "bg2": "#223127", "line": "rgba(230,235,221,.13)", "accent": "#9ACB72", "needs": "#EBB64A", "finding": "#9ACB72", "sSky1": "#0A181A", "sSky2": "#1B312D", "sOrb": "#EDEBD9", "sFar": "#1A2F2A", "sPines": "#152723", "sMid": "#1D382B", "sGround": "#14281B", "sGrass": "#1B3625", "sTrunk": "#3B2A1E", "sBark": "#2B1E15", "sLeaf1": "#10241A", "sLeaf2": "#163021", "sLeaf3": "#1D3D29", "sTag": "#C9A374", "sTagLine": "#5E4630", "sStump": "#4A3424", "sStumpTop": "#80633F", "sRing": "#5E4630", "sRay": "rgba(255,255,236,0)", "sCap": "#9A3C2A"},
  },
  rustic: {
    id: 'rustic', name: "Rustic", where: "A cabin by the hearth", need: "PINNED FOR YOU",
    line: "The roll rests on a plate by the fire. Threads are recipe cards pinned to the board; what needs you is a note with a red pin.",
    presenter: 'cards', accessory: null, pet: { name: "Nutmeg", kind: "cat" }, displayScale: 1.0,
    fonts: {"display": "Alegreya_800ExtraBold", "ui": "AlegreyaSans_400Regular", "uiSemi": "AlegreyaSans_700Bold", "mono": "CourierPrime_400Regular", "hand": "Caveat_600SemiBold"},
    fontFiles: { Alegreya_800ExtraBold, AlegreyaSans_400Regular, AlegreyaSans_700Bold, Caveat_600SemiBold, CourierPrime_400Regular },
    day: {"ink": "#2B1D14", "ink2": "#5A4636", "ink3": "#76604C", "raise": "#FAF4EA", "bg2": "#EFE2CD", "line": "rgba(43,29,20,.13)", "accent": "#A4452C", "needs": "#D9962A", "finding": "#4F6A3A", "sPlank": "#D8B88C", "sPlankLine": "#C29E70", "sKnot": "#B48D60", "sCork": "#C79A62", "sCorkDot": "#B5874F", "sFrame": "#6E4A2B", "sCard": "#FFFDF7", "sCardLine": "#A9C7E2", "sRed": "#D2564A", "sSky1": "#D5E4EE", "sSky2": "#F2EEE6", "sSnow": "#FFFFFF", "sHill": "#E9EEF0", "sHill2": "#D3DDE4", "sPine": "#7F948C", "sStone": "#B7AA9A", "sStoneDark": "#8F8273", "sFirebox": "#2A1B12", "sMantel": "#7A5131", "sTable": "#9A6438", "sTableTop": "#B47A48", "sCloth": "#C8473D", "sCloth2": "#F3E9DB", "sPlate": "#F7F2EB", "sPlateRim": "#E2D7C9", "sGlow": "rgba(255,150,70,.22)", "sOrb": "#FFFFFF"},
    night: {"ink": "#F1E4D0", "ink2": "#CDB9A0", "ink3": "#A8927A", "raise": "#2A1F18", "bg2": "#3A2C22", "line": "rgba(241,228,208,.13)", "accent": "#E58B5A", "needs": "#EBA94A", "finding": "#B3C482", "sPlank": "#4A3322", "sPlankLine": "#3B281A", "sKnot": "#33231A", "sCork": "#6E5134", "sCorkDot": "#5E432A", "sFrame": "#2E1E13", "sCard": "#EFE6D4", "sCardLine": "#7A93A8", "sRed": "#B5473D", "sSky1": "#0E1730", "sSky2": "#2B3557", "sSnow": "#DDE6F2", "sHill": "#8FA0B8", "sHill2": "#6E7F99", "sPine": "#2C3A4A", "sStone": "#6E6257", "sStoneDark": "#4E453D", "sFirebox": "#120A06", "sMantel": "#4A3020", "sTable": "#5A3820", "sTableTop": "#704729", "sCloth": "#8E3530", "sCloth2": "#BFB09B", "sPlate": "#DCD3C6", "sPlateRim": "#BCAF9F", "sGlow": "rgba(255,140,60,.42)", "sOrb": "#F4EBD0"},
  },
  space: {
    id: 'space', name: "Space", where: "Out exploring", need: "INCOMING TRANSMISSION",
    line: "The roll floats in its helmet. Threads are satellites on its orbit; what needs you arrives as a transmission.",
    presenter: 'satellites', accessory: 'helmet', pet: { name: "Anise", kind: "eight-armed alien" }, displayScale: 0.76,
    fonts: {"display": "Unbounded_500Medium", "ui": "Sora_400Regular", "uiSemi": "Sora_600SemiBold", "mono": "MartianMono_400Regular", "hand": "Sora_600SemiBold"},
    fontFiles: { Unbounded_500Medium, Sora_400Regular, Sora_600SemiBold, MartianMono_400Regular },
    day: {"ink": "#14172E", "ink2": "#41476A", "ink3": "#5A6184", "raise": "#F8F9FD", "bg2": "#E6E8F4", "line": "rgba(20,23,46,.13)", "accent": "#3D4FD1", "needs": "#E79A2A", "finding": "#7A3FD0", "sSky1": "#B9B4EE", "sSky2": "#F8D9CB", "sStar": "#FFFFFF", "sStarO": ".5", "sPlanet": "#E58C6A", "sRing": "#F6CDB0", "sPlanet2": "#7C8CE0", "sGround": "#A99BD6", "sGround2": "#9384C9", "sCrater": "#8070B8", "sOrbit": "rgba(40,30,90,.35)", "sSat": "#F4F3FC", "sPanel": "#3D4FD1", "sRim": "#FFFFFF", "sGlass": "rgba(255,255,255,.24)", "sGalaxyO": "0", "sLabel": "rgba(255,255,255,.78)"},
    night: {"ink": "#E4E8F7", "ink2": "#A8B0CC", "ink3": "#8089A8", "raise": "#141829", "bg2": "#1E2340", "line": "rgba(228,232,247,.13)", "accent": "#A9B8FF", "needs": "#FFC46B", "finding": "#C58BFF", "sSky1": "#05060F", "sSky2": "#171C40", "sStar": "#FFFFFF", "sStarO": "1", "sPlanet": "#C8643F", "sRing": "#E8A982", "sPlanet2": "#3E4AA8", "sGround": "#1B2050", "sGround2": "#13173C", "sCrater": "#0F1233", "sOrbit": "rgba(200,210,255,.3)", "sSat": "#CDD2EC", "sPanel": "#6C7DFF", "sRim": "#C9D3FF", "sGlass": "rgba(170,190,255,.14)", "sGalaxyO": "1", "sLabel": "rgba(20,24,41,.78)"},
  },
  tide: {
    id: 'tide', name: "Tide", where: "Under the sea", need: "WASHED UP FOR YOU",
    line: "The roll rests in an open clam. Threads are bubbles, newest highest; what needs you washes up as a bottle.",
    presenter: 'bubbles', accessory: null, pet: { name: "Saffron", kind: "fish" }, displayScale: 1.0,
    fonts: {"display": "Fredoka_600SemiBold", "ui": "Figtree_400Regular", "uiSemi": "Figtree_600SemiBold", "mono": "DMMono_400Regular", "hand": "Fredoka_600SemiBold"},
    fontFiles: { Fredoka_600SemiBold, Figtree_400Regular, Figtree_600SemiBold, DMMono_400Regular },
    day: {"ink": "#0B2A33", "ink2": "#265660", "ink3": "#3A6A73", "raise": "#F2FAFA", "bg2": "#DCEFEF", "line": "rgba(11,42,51,.13)", "accent": "#1F7A8C", "needs": "#E8A23A", "finding": "#1F6E5A", "sW1": "#C4EEEC", "sW2": "#2C8A9B", "sRay": "rgba(255,255,255,.3)", "sKelp": "#3E8A5C", "sKelp2": "#2F7550", "sSand": "#EAD9AF", "sSand2": "#D8C190", "sCoral": "#F28A6B", "sCoral2": "#F7B9A3", "sRock": "#8FA6A4", "sBub": "rgba(255,255,255,.4)", "sBubRim": "rgba(255,255,255,.85)", "sShell": "#F5DCC8", "sShell2": "#E2B79C", "sSnow": "rgba(255,255,255,0)"},
    night: {"ink": "#D8EEF0", "ink2": "#A3C6CB", "ink3": "#7FA4AA", "raise": "#0F2D38", "bg2": "#163A46", "line": "rgba(216,238,240,.13)", "accent": "#5CC8D7", "needs": "#F2C46B", "finding": "#7FD6C2", "sW1": "#0E3746", "sW2": "#03121A", "sRay": "rgba(255,255,255,0)", "sKelp": "#164A3A", "sKelp2": "#10382C", "sSand": "#34464A", "sSand2": "#26363A", "sCoral": "#8A4A40", "sCoral2": "#A86A5C", "sRock": "#2E4447", "sBub": "rgba(160,230,240,.1)", "sBubRim": "rgba(160,230,240,.5)", "sShell": "#B09A8A", "sShell2": "#8A7466", "sSnow": "rgba(190,240,245,.7)"},
  },
};
