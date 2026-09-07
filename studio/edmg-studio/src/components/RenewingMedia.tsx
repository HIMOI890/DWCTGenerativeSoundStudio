import React, { useRef, type AudioHTMLAttributes, type VideoHTMLAttributes } from "react";
import { usePreservedMediaSource } from "../hooks/useSignedProjectMedia";

export function RenewingVideo({
  sourceUrl,
  ...props
}: VideoHTMLAttributes<HTMLVideoElement> & { sourceUrl: string }) {
  const ref = useRef<HTMLVideoElement | null>(null);
  usePreservedMediaSource(ref, sourceUrl);
  return <video {...props} ref={ref} />;
}

export function RenewingAudio({
  sourceUrl,
  ...props
}: AudioHTMLAttributes<HTMLAudioElement> & { sourceUrl: string }) {
  const ref = useRef<HTMLAudioElement | null>(null);
  usePreservedMediaSource(ref, sourceUrl);
  return <audio {...props} ref={ref} />;
}
