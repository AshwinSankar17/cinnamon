/**
 * The five world backdrops, ported from the approved mockup (docs/design/cinnamon/Cinnamon.dc.html) in its
 * 390×844 design space. Static art only: threads, the waiting card, the roll, the companion and ambient life are
 * separate layers placed with the same frame (see Scene.tsx). Generated from the mockup's markup, then hand-joined.
 */
/* eslint-disable react/no-array-index-key */
import { Circle, ClipPath, Defs, Ellipse, G, LinearGradient, Path, Pattern, RadialGradient, Rect, Stop } from 'react-native-svg';
import type { ReactElement } from 'react';
import type { Tokens, WorldId } from '@/src/worlds';

const rnd = (i: number) => {
  const x = Math.sin(i * 127.1 + 311.7) * 43758.5453;
  return x - Math.floor(x);
};
const f1 = (v: number) => Number(v.toFixed(1));

/** Procedural details of the mockup (pines, planks, stones, snow, stars, galaxy), computed once. */
const STATIC = (() => {
  let pines = '';
  for (let x = -10; x < 400; x += 22) {
    const y = 452 - Math.sin(x / 40) * 8 - (x > 200 ? (x - 200) * 0.08 : 0);
    const h = 20 + rnd(x) * 16;
    pines += `M${x} ${f1(y + 4)}L${x + 9} ${f1(y - h)}L${x + 18} ${f1(y + 4)}Z`;
  }
  let planks = '';
  for (let x = 38; x < 390; x += 39) planks += `M${x} 0V600`;
  let stones = '';
  for (let r = 0; r < 7; r++) {
    const y = 398 + r * 29;
    stones += `M254 ${y}H390`;
    for (let x = 254 + (r % 2) * 22; x < 390; x += 44) stones += `M${x} ${y}V${y + 29}`;
  }
  let windowSnow = '';
  for (let i = 0; i < 26; i++) windowSnow += `M${f1(242 + rnd(i) * 118)} ${f1(210 + rnd(i + 40) * 108)}h.01`;
  const stars = Array.from({ length: 40 }, (_, i) => ({ x: f1(rnd(i) * 390), y: f1(rnd(i + 70) * 640), r: rnd(i + 5) > 0.85 ? 1.6 : 0.9 }));
  let galaxy = '';
  for (let arm = 0; arm < 2; arm++)
    for (let i = 0; i <= 40; i++) {
      const s = i / 40;
      const th = s * 4.2 + arm * Math.PI;
      const r = 4 + s * 70;
      galaxy += `${i ? ' L' : 'M'}${f1(300 + r * Math.cos(th))} ${f1(300 + r * Math.sin(th) * 0.45)}`;
    }
  const tideSnow = Array.from({ length: 26 }, (_, i) => ({ x: f1(rnd(i) * 390), y: f1(rnd(i + 20) * 600), r: f1(0.6 + rnd(i + 3) * 1.2) }));
  return { pines, planks, stones, windowSnow, stars, galaxy, tideSnow };
})();

export function SceneDefs({ t }: { t: Tokens }) {
  return (
    <Defs>
      <LinearGradient id="bkWin" x1={0} y1={0} x2={0} y2={1}>
        <Stop offset={0} stopColor={t.sSky1} />
        <Stop offset={1} stopColor={t.sSky2} />
      </LinearGradient>
      <RadialGradient id="bkGlow">
        <Stop offset={0} stopColor={t.sGlow} />
        <Stop offset={1} stopColor="rgba(255,214,150,0)" />
      </RadialGradient>
      <Pattern id="bkTiles" width={44} height={22} patternUnits="userSpaceOnUse">
        <Rect width={44} height={22} fill={t.sTile} />
        <Path d="M0 21.5H44M22 0V11M0 10.5H44M44 11V22M0 11V22" fill="none" strokeWidth={1.2} stroke={t.sTileLine} />
      </Pattern>
      <LinearGradient id="fsSky" x1={0} y1={0} x2={0} y2={1}>
        <Stop offset={0} stopColor={t.sSky1} />
        <Stop offset={.7} stopColor={t.sSky2} />
      </LinearGradient>
      <LinearGradient id="rsWin" x1={0} y1={0} x2={0} y2={1}>
        <Stop offset={0} stopColor={t.sSky1} />
        <Stop offset={1} stopColor={t.sSky2} />
      </LinearGradient>
      <RadialGradient id="rsGlow">
        <Stop offset={0} stopColor={t.sGlow} />
        <Stop offset={1} stopColor="rgba(255,150,70,0)" />
      </RadialGradient>
      <Pattern id="rsCheck" width={16} height={16} patternUnits="userSpaceOnUse">
        <Rect width={16} height={16} fill={t.sCloth2} />
        <Rect width={8} height={8} fill={t.sCloth} />
        <Rect x={8} y={8} width={8} height={8} fill={t.sCloth} />
      </Pattern>
      <LinearGradient id="spSky" x1={0} y1={0} x2={0} y2={1}>
        <Stop offset={0} stopColor={t.sSky1} />
        <Stop offset={1} stopColor={t.sSky2} />
      </LinearGradient>
      <RadialGradient id="spNeb" cx={.5} cy={.5} r={.5}>
        <Stop offset={0} stopColor="rgba(160,120,255,.35)" />
        <Stop offset={1} stopColor="rgba(160,120,255,0)" />
      </RadialGradient>
      <LinearGradient id="tdSea" x1={0} y1={0} x2={0} y2={1}>
        <Stop offset={0} stopColor={t.sW1} />
        <Stop offset={1} stopColor={t.sW2} />
      </LinearGradient>
      <ClipPath id="rsWinClip">
    <Rect x={240} y={206} width={122} height={116} />
  </ClipPath>
    </Defs>
  );
}

function Bakery({ t, dark, plain }: { t: Tokens; dark: boolean; plain?: boolean }) {
  return (
    <>
      <Rect width={390} height={844} fill={t.sWall} />
      <Rect y={330} width={390} height={232} fill="url(#bkTiles)" />
      <Circle cx={310} cy={380} r={250} fill="url(#bkGlow)" />
      {plain ? null : (<>
      <G transform="translate(252 312)">
        <Rect width={118} height={122} rx={12} fill={t.sFrame} />
        <Rect x={8} y={8} width={102} height={106} rx={7} fill="url(#bkWin)" />
        <Circle cx={80} cy={36} r={13} fill={t.sOrb} />
        <Path d="M8 114V88L22 76L36 88V78H54V92L70 80L86 93V84H110V114Z" fill={t.sRoofs} />
        <Rect x={57} y={8} width={4} height={106} fill={t.sFrame} />
        <Rect x={8} y={59} width={102} height={4} fill={t.sFrame} />
        <Rect x={-6} y={118} width={130} height={9} rx={3} fill={t.sWoodDark} />
      </G>
      <G transform="translate(20 352)">
        <Rect x={0} y={6} width={34} height={42} rx={9} fill={t.sGlass} stroke="rgba(255,255,255,.7)" />
        <Rect x={4} y={22} width={26} height={22} rx={6} fill="#F7F2EA" />
        <Rect x={3} y={0} width={28} height={8} rx={3} fill={t.sWoodDark} />
        <Rect x={44} y={12} width={32} height={36} rx={9} fill={t.sGlass} stroke="rgba(255,255,255,.7)" />
        <Path d="M50 46V20M56 46V18M62 46V21M68 46V19" stroke="#8A4320" strokeWidth={4} strokeLinecap="round" />
        <Rect x={47} y={6} width={26} height={8} rx={3} fill={t.sWoodDark} />
        <Rect x={86} y={16} width={30} height={32} rx={9} fill={t.sGlass} stroke="rgba(255,255,255,.7)" />
        <Rect x={90} y={28} width={22} height={16} rx={5} fill="#E9C26E" />
        <Rect x={89} y={10} width={24} height={8} rx={3} fill={t.sWoodDark} />
        <Rect x={-6} y={48} width={134} height={7} rx={2} fill={t.sWoodDark} />
      </G>
      <Rect x={12} y={212} width={366} height={5} rx={2.5} fill={t.sRail} />
      </>)}
      <Rect y={560} width={390} height={22} fill={t.sTop} />
      <Rect y={582} width={390} height={262} fill={t.sWood} />
      <Path d="M0 598H390M98 598V844M196 598V844M294 598V844" strokeWidth={2} stroke={t.sWoodDark} opacity={.5} />
      {plain ? null : (<>
      <G transform="translate(190 566)">
        <Ellipse rx={100} ry={11} fill="none" strokeWidth={3} stroke={t.sRack} />
        <Path d="M-84 -5H84M-96 0H96M-84 5H84" strokeWidth={1.6} stroke={t.sRack} opacity={.8} />
      </G>
      </>)}
    </>
  );
}

function Forest({ t, dark, plain }: { t: Tokens; dark: boolean; plain?: boolean }) {
  return (
    <>
      <Rect width={390} height={844} fill="url(#fsSky)" />
      {plain ? null : (<>
      <Circle cx={64} cy={268} r={30} fill={t.sOrb} />
      </>)}
      <Path d="M0 470C70 438 150 452 230 432C300 416 350 428 390 414V844H0Z" fill={t.sFar} />
      <Path d={STATIC.pines} fill={t.sPines} />
      <Path d="M0 520C90 492 180 510 250 496C320 482 360 490 390 486V844H0Z" fill={t.sMid} />
      {plain ? null : (<>
      <Path d="M150 230L60 620H120L190 230ZM235 240L200 620H250L262 240Z" fill={t.sRay} />
      </>)}
      <Path d="M258 660C268 600 266 520 270 440C274 380 272 320 266 262H334C328 320 328 380 332 440C336 520 338 600 352 660Z" fill={t.sTrunk} />
      <Path d="M232 668C246 650 256 640 262 612L272 640C264 652 252 662 232 668ZM372 668C356 652 346 640 344 610L334 640C340 652 352 662 372 668Z" fill={t.sTrunk} />
      <Path d="M286 300C282 380 290 470 284 560M312 290C318 370 308 460 316 580M298 420C296 470 300 520 296 600" fill="none" strokeWidth={3} strokeLinecap="round" stroke={t.sBark} />
      <Ellipse cx={302} cy={470} rx={9} ry={12} fill={t.sBark} />
      <Path d="M282 378C224 368 150 340 22 334V344C150 352 224 386 282 396Z" fill={t.sTrunk} />
      <Circle cx={300} cy={296} r={96} fill={t.sLeaf1} />
      <Circle cx={214} cy={316} r={70} fill={t.sLeaf2} />
      <Circle cx={366} cy={334} r={62} fill={t.sLeaf2} />
      <Circle cx={138} cy={330} r={54} fill={t.sLeaf1} />
      <Circle cx={258} cy={260} r={56} fill={t.sLeaf3} />
      <Circle cx={348} cy={256} r={48} fill={t.sLeaf3} />
      <Circle cx={62} cy={346} r={40} fill={t.sLeaf2} />
      <Circle cx={180} cy={284} r={34} fill={t.sLeaf3} />
      <Path d="M0 604C70 592 140 600 210 594C280 588 340 596 390 590V844H0Z" fill={t.sGround} />
      <Path d="M18 606l6-14 3 14 5-10 2 10M340 600l5-12 3 12 4-9 2 9M228 604l4-10 3 10" fill="none" strokeWidth={2.2} strokeLinecap="round" stroke={t.sGrass} />
      <Path d="M24 640C40 610 70 604 92 612C70 612 52 624 44 644ZM30 650C20 624 0 616 -6 618" fill="none" strokeWidth={3} strokeLinecap="round" stroke={t.sGrass} />
      {plain ? null : (<>
      <Path d="M82 596C82 620 86 640 80 664H220C214 640 218 620 218 596Z" fill={t.sStump} />
      <Ellipse cx={150} cy={596} rx={68} ry={14} fill={t.sStumpTop} />
      <Ellipse cx={150} cy={596} rx={48} ry={9} fill="none" strokeWidth={1.4} stroke={t.sRing} />
      <Ellipse cx={150} cy={596} rx={28} ry={5} fill="none" strokeWidth={1.4} stroke={t.sRing} />
      <G transform="translate(232 652)">
        <Rect x={-3} y={-14} width={6} height={14} rx={3} fill="#EFE3CF" />
        <Path d="M-13 -12C-13 -26 13 -26 13 -12Z" fill={t.sCap} />
        <Circle cx={-4} cy={-18} r={2} fill="#fff" opacity={.85} />
        <Circle cx={5} cy={-16} r={1.6} fill="#fff" opacity={.85} />
      </G>
      </>)}
    </>
  );
}

function Rustic({ t, dark, plain }: { t: Tokens; dark: boolean; plain?: boolean }) {
  return (
    <>
      <Rect width={390} height={844} fill={t.sPlank} />
      <Path d={STATIC.planks} strokeWidth={2} stroke={t.sPlankLine} />
      <Ellipse cx={46} cy={470} rx={5} ry={8} fill={t.sKnot} />
      <Ellipse cx={206} cy={372} rx={4} ry={7} fill={t.sKnot} />
      <Circle cx={320} cy={520} r={230} fill="url(#rsGlow)" />
      {plain ? null : (<>
      <Rect x={18} y={200} width={196} height={150} rx={8} fill={t.sFrame} />
      <Rect x={26} y={208} width={180} height={134} rx={4} fill={t.sCork} />
      <Path d="M40 228h.01M90 300h.01M150 220h.01M186 330h.01M60 330h.01M170 270h.01" strokeWidth={3} strokeLinecap="round" stroke={t.sCorkDot} />
      <Rect x={232} y={198} width={138} height={132} rx={8} fill={t.sFrame} />
      <Rect x={240} y={206} width={122} height={116} rx={4} fill="url(#rsWin)" />
      <Circle cx={336} cy={236} r={11} fill={t.sOrb} opacity={.9} />
      <Path d="M240 282C270 266 300 276 330 262C344 256 356 258 362 256V322H240Z" fill={t.sHill2} />
      <Path d="M240 298C276 284 310 296 362 284V322H240Z" fill={t.sHill} />
      <Path d="M262 288l7-18 7 18ZM282 292l6-15 6 15ZM330 280l7-20 7 20Z" fill={t.sPine} />
      <G clipPath="url(#rsWinClip)">
        <Path d={STATIC.windowSnow} strokeWidth={2.4} strokeLinecap="round" stroke={t.sSnow} />
      </G>
      <Rect x={299} y={206} width={4} height={116} fill={t.sFrame} />
      <Rect x={240} y={262} width={122} height={4} fill={t.sFrame} />
      <Rect x={244} y={384} width={150} height={14} rx={3} fill={t.sMantel} />
      <Path d="M254 398H390V600H254Z" fill={t.sStone} />
      <Path d={STATIC.stones} fill="none" strokeWidth={2} stroke={t.sStoneDark} />
      <Path d="M276 600V490C276 448 368 448 368 490V600Z" fill={t.sFirebox} />
      <Path d="M290 590C292 560 306 556 302 528C318 548 312 566 322 572C326 556 334 548 332 532C348 556 352 578 350 590Z" fill="#E8742A" />
      <Path d="M302 590C304 572 312 566 312 552C322 566 320 578 326 582C330 572 334 566 334 556C342 572 342 584 340 590Z" fill="#FFC14A" />
      <Path d="M286 594H360" stroke="#3A2214" strokeWidth={5} strokeLinecap="round" />
      <G transform="translate(262 384)">
        <Rect x={0} y={-26} width={16} height={26} rx={3} fill="#F2E6CF" />
        <Path d="M8 -26V-32" stroke="#3A2214" strokeWidth={1.5} />
        <Path d="M8 -33C4 -38 7 -44 8 -46C9 -44 12 -38 8 -33Z" fill="#FFC14A" />
      </G>
      <G transform="translate(350 384)">
        <Rect x={-14} y={-30} width={28} height={30} rx={8} fill="rgba(255,255,255,.45)" stroke="rgba(255,255,255,.7)" />
        <Path d="M-8 -2V-22M-2 -2V-24M4 -2V-21" stroke="#8A4320" strokeWidth={4} strokeLinecap="round" />
      </G>
      </>)}
      <Path d="M0 604L390 590V844H0Z" fill={t.sTable} />
      <Path d="M0 604L390 590V600L0 616Z" fill={t.sTableTop} />
      <Path d="M0 640L390 628M0 700L390 690M0 770L390 762" strokeWidth={1.5} opacity={.35} stroke={t.sFrame} />
      {plain ? null : (<>
      <Path d="M54 606L236 598L248 640L46 650Z" fill="url(#rsCheck)" opacity={.95} />
      <Ellipse cx={160} cy={610} rx={96} ry={19} fill={t.sPlateRim} />
      <Ellipse cx={160} cy={607} rx={80} ry={14} fill={t.sPlate} />
      </>)}
    </>
  );
}

function Space({ t, dark, plain }: { t: Tokens; dark: boolean; plain?: boolean }) {
  return (
    <>
      <Rect width={390} height={844} fill="url(#spSky)" />
      <G opacity={Number(t.sStarO)}>
        {STATIC.stars.map((s, i) => <Circle key={i} cx={s.x} cy={s.y} r={s.r} fill={t.sStar} />)}
      </G>
      <G opacity={Number(t.sGalaxyO)}>
        <Ellipse cx={300} cy={300} rx={90} ry={44} fill="url(#spNeb)" />
        <Path d={STATIC.galaxy} fill="none" stroke="#E9C9FF" strokeWidth={2.4} strokeLinecap="round" opacity={0.55} />
        <Circle cx={300} cy={300} r={5} fill="#FFF4D6" />
      </G>
      {plain ? null : (<>
      <Circle cx={62} cy={292} r={34} fill={t.sPlanet} />
      <Path d="M62 258a34 34 0 0 1 0 68a22 34 0 0 0 0 -68Z" fill="rgba(0,0,0,.12)" />
      <Ellipse cx={62} cy={294} rx={60} ry={11} fill="none" strokeWidth={5} transform="rotate(-14 62 294)" stroke={t.sRing} />
      <Circle cx={350} cy={420} r={9} fill={t.sPlanet2} />
      </>)}
      <Path d="M-20 680C80 640 300 640 410 680V844H-20Z" fill={t.sGround} />
      <Ellipse cx={80} cy={700} rx={30} ry={6} fill={t.sCrater} />
      <Ellipse cx={300} cy={716} rx={22} ry={5} fill={t.sCrater} />
      <Ellipse cx={200} cy={676} rx={14} ry={3} fill={t.sCrater} />
      {plain ? null : (<>
      <G transform="translate(60 670)">
        <Path d="M0 -40C8 -30 10 -14 8 0H-8C-10 -14 -8 -30 0 -40Z" fill={t.sSat} />
        <Circle cx={0} cy={-22} r={4} fill={t.sPanel} />
        <Path d="M-8 -6L-16 4H-6ZM8 -6L16 4H6Z" fill="#E0663A" />
      </G>
      <Ellipse cx={200} cy={505} rx={176} ry={96} fill="none" strokeWidth={1.5} strokeDasharray="3 6" transform="rotate(-6 200 505)" stroke={t.sOrbit} />
      </>)}
    </>
  );
}

function Tide({ t, dark, plain }: { t: Tokens; dark: boolean; plain?: boolean }) {
  return (
    <>
      <Rect width={390} height={844} fill="url(#tdSea)" />
      <Path d="M20 0L-40 600H40L90 0ZM150 0L120 640H180L200 0ZM270 0L300 600H350L320 0Z" fill={t.sRay} />
      {dark ? STATIC.tideSnow.map((s, i) => <Circle key={i} cx={s.x} cy={s.y} r={s.r} fill={t.sSnow} />) : null}
      <G>
        <Path d="M34 640C20 560 50 500 30 430C14 370 40 320 30 270" fill="none" strokeWidth={10} strokeLinecap="round" stroke={t.sKelp} />
        <Path d="M30 470C44 460 54 448 56 432M34 380C20 372 12 360 12 344M32 300C46 292 52 280 52 266" fill="none" strokeWidth={7} strokeLinecap="round" stroke={t.sKelp} />
      </G>
      <G>
        <Path d="M64 650C56 590 76 540 62 480C52 440 70 400 62 360" fill="none" strokeWidth={8} strokeLinecap="round" stroke={t.sKelp2} />
      </G>
      <G>
        <Path d="M360 650C376 580 346 520 366 450C380 400 356 350 368 300" fill="none" strokeWidth={10} strokeLinecap="round" stroke={t.sKelp} />
        <Path d="M366 500C352 492 344 478 344 462M364 400C378 390 384 378 384 362" fill="none" strokeWidth={7} strokeLinecap="round" stroke={t.sKelp} />
      </G>
      <Path d="M-10 610C80 596 160 606 230 598C300 590 350 600 400 594V844H-10Z" fill={t.sSand} />
      <Path d="M20 640C60 634 100 642 140 636M220 650C260 644 300 652 350 644M60 700C110 694 160 702 210 696" fill="none" strokeWidth={2} strokeLinecap="round" stroke={t.sSand2} />
      <Path d="M300 606C300 584 312 572 322 566M322 606C324 590 336 582 346 580M312 590C304 580 296 578 290 580" fill="none" strokeWidth={7} strokeLinecap="round" stroke={t.sCoral} />
      <Path d="M82 610C82 596 74 586 66 582M82 600C88 590 98 586 106 586" fill="none" strokeWidth={6} strokeLinecap="round" stroke={t.sCoral2} />
      <Ellipse cx={44} cy={612} rx={26} ry={11} fill={t.sRock} />
      <G transform="translate(262 640) rotate(14)">
        <Path d="M0 -12L3 -4L12 -4L5 2L8 11L0 5L-8 11L-5 2L-12 -4L-3 -4Z" fill="#F2A35E" />
      </G>
      {plain ? null : (<>
      <Path d="M98 560C100 488 290 488 292 560C250 548 140 548 98 560Z" fill={t.sShell} />
      <Path d="M118 552C140 510 160 500 195 498M150 548C164 516 178 504 195 500M240 548C226 516 212 504 195 500M272 552C250 510 230 500 195 498" fill="none" strokeWidth={2} stroke={t.sShell2} />
      <Path d="M86 600C96 626 294 626 304 600C290 584 100 584 86 600Z" fill={t.sShell2} />
      </>)}
    </>
  );
}

/** `plain` drops the furniture (windows, hearth, stump, planets, the clam) and keeps the setting: walls, floors, sky,
 *  sea, ground. The mirrored continuation past the scene's edges uses it, so nothing appears twice. */
export const BACKDROPS: Record<WorldId, (p: { t: Tokens; dark: boolean; plain?: boolean }) => ReactElement> = {
  bakery: Bakery,
  forest: Forest,
  rustic: Rustic,
  space: Space,
  tide: Tide,
};
