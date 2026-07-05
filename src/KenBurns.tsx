import React from 'react';
import {
  AbsoluteFill,
  Easing,
  Img,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';
import {loadFont as loadAnton} from '@remotion/google-fonts/Anton';
import {loadFont as loadArchivo} from '@remotion/google-fonts/Archivo';
import type {PhotoBeat} from './beats';

const anton = loadAnton();
const archivo = loadArchivo();

const ACCENT = '#FF7A00';

type Motion = 'punch' | 'kb' | 'drift';
type Shot = {image: string; credit?: string; focusX?: number; focusY?: number};
type PhotoBeatV3 = PhotoBeat & {style?: Motion; shots?: Shot[]};

const resolveSrc = (image: string): string =>
  image.startsWith('http') ? image : staticFile(image);

export const KenBurnsBeat: React.FC<{beat: PhotoBeat; index: number}> = ({
  beat,
  index,
}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const b = beat as PhotoBeatV3;
  const totalFrames = Math.round(beat.duration * fps);

  // Montage: multiple shots hard-cut inside one beat. Falls back to the
  // single beat-level image when no shots array exists.
  const shots: Shot[] =
    b.shots && b.shots.length
      ? b.shots
      : [{image: beat.image, credit: beat.credit, focusX: beat.focusX, focusY: beat.focusY}];

  const shotLen = Math.max(1, Math.floor(totalFrames / shots.length));
  const s = Math.min(shots.length - 1, Math.floor(frame / shotLen));
  const f = frame - s * shotLen; // frame local to the current shot
  const thisShotLen = s === shots.length - 1 ? totalFrames - s * shotLen : shotLen;
  const shot = shots[s];

  const fx = (shot.focusX ?? 0.5) * 100;
  const fy = (shot.focusY ?? 0.32) * 100;

  // Motion per shot. Montages alternate punch/drift for rhythm;
  // single-shot beats keep the v2 trio rotation.
  const motion: Motion =
    shots.length > 1
      ? ((index + s) % 2 === 0 ? 'punch' : 'drift')
      : b.style ?? (['punch', 'kb', 'drift'] as Motion[])[index % 3];

  let scale = 1.1;
  let tx = 0;
  let rot = 0;
  let flash = 0;

  if (motion === 'punch') {
    const spr = spring({frame: f, fps, config: {damping: 10, mass: 0.6}});
    scale = 1.3 - 0.16 * spr;
    rot = 1.2 * (1 - spr);
    tx = interpolate(f, [0, thisShotLen], [0, (index + s) % 2 === 0 ? 10 : -10], {
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
    });
    flash = interpolate(f, [0, 5], [0.12, 0], {
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
    });
  } else if (motion === 'kb') {
    const zoomIn = beat.zoom ? beat.zoom === 'in' : index % 2 === 0;
    const [from, to] = zoomIn ? [1.06, 1.17] : [1.17, 1.06];
    scale = interpolate(f, [0, thisShotLen], [from, to], {
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
    });
    tx = interpolate(
      f,
      [0, thisShotLen],
      [-10 * (index % 2 === 0 ? 1 : -1), 10 * (index % 2 === 0 ? 1 : -1)],
      {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'}
    );
  } else {
    scale = 1.14;
    const dir = (index + s) % 2 === 0 ? 1 : -1;
    tx = interpolate(f, [0, thisShotLen], [-26 * dir, 26 * dir], {
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
    });
    rot = interpolate(f, [0, thisShotLen], [-0.6 * dir, 0.6 * dir], {
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
    });
  }

  // Caption belongs to the beat and holds across shot cuts.
  const capSpring = spring({
    frame: Math.max(0, frame - 4),
    fps,
    config: {damping: 12, mass: 0.7},
  });
  const capY = 46 * (1 - capSpring);
  const capOpacity = interpolate(frame, [4, 12], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const barScale = interpolate(frame, [8, 22], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.out(Easing.cubic),
  });

  const creditText = shot.credit ? `${shot.credit}-Imagn Images` : 'Imagn Images';

  return (
    <AbsoluteFill style={{overflow: 'hidden', backgroundColor: '#000'}}>
      <Img
        key={s}
        src={resolveSrc(shot.image)}
        style={{
          width: '100%',
          height: '100%',
          objectFit: 'cover',
          objectPosition: `${fx}% ${fy}%`,
          transform: `scale(${scale}) translateX(${tx}px) rotate(${rot}deg)`,
          transformOrigin: `${fx}% ${fy}%`,
        }}
      />

      <AbsoluteFill
        style={{
          background:
            'linear-gradient(to bottom, rgba(0,0,0,0.12) 0%, rgba(0,0,0,0) 22%, rgba(0,0,0,0) 55%, rgba(0,0,0,0.66) 100%)',
        }}
      />

      {flash > 0 ? (
        <AbsoluteFill style={{backgroundColor: '#FFFFFF', opacity: flash}} />
      ) : null}

      {beat.text ? (
        <div
          style={{
            position: 'absolute',
            left: 0,
            right: 0,
            bottom: 200,
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            padding: '0 64px',
            opacity: capOpacity,
            transform: `translateY(${capY}px)`,
          }}
        >
          <div style={{transform: 'skewX(-3deg)'}}>
            <div
              style={{
                fontFamily: anton.fontFamily,
                fontSize: 74,
                lineHeight: 1.05,
                color: '#FFFFFF',
                textAlign: 'center',
                textTransform: 'uppercase',
                letterSpacing: 1,
                textShadow:
                  '0 6px 22px rgba(0,0,0,0.9), 0 2px 4px rgba(0,0,0,0.9)',
              }}
            >
              {beat.text}
            </div>
          </div>
          <div
            style={{
              marginTop: 20,
              width: 150,
              height: 10,
              backgroundColor: ACCENT,
              transform: `scaleX(${barScale})`,
              boxShadow: '0 2px 12px rgba(0,0,0,0.7)',
            }}
          />
        </div>
      ) : null}

      <div
        style={{
          position: 'absolute',
          left: 34,
          bottom: 28,
          fontFamily: archivo.fontFamily,
          fontSize: 23,
          fontWeight: 500,
          color: 'rgba(255,255,255,0.8)',
          letterSpacing: 0.6,
          textShadow: '0 1px 5px rgba(0,0,0,0.9)',
        }}
      >
        {creditText}
      </div>
    </AbsoluteFill>
  );
};
