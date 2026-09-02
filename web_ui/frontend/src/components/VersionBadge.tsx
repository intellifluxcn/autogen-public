// App version badge for the top bar.
//
// Displays semver + short git SHA in a single monospace token, e.g.
// "v0.1.0+a1b2c3d", following the SemVer 2.0 build-metadata convention used
// by GitHub Releases, Sentry, Grafana, etc. The badge itself is intentionally
// terse; full build time is in the hover tooltip so ops can verify which
// release a user is on without crowding the chrome.

import { useTranslation } from '../hooks/useTranslation';

function pad(n: number): string {
  return n.toString().padStart(2, '0');
}

function formatLocalBuildTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}`
  );
}

export function VersionBadge() {
  const { t } = useTranslation();
  const version = __APP_VERSION__;
  const sha = __APP_BUILD_SHA__;
  const buildTime = __APP_BUILD_TIME__;

  // SemVer 2.0 build metadata is appended after `+`. Omit when SHA is the
  // sentinel "unknown" (e.g. CI build without git history) so we never
  // render a misleading commit suffix.
  const label =
    sha && sha !== 'unknown' ? `v${version}+${sha}` : `v${version}`;

  const tooltip = t('version.badge_tooltip', {
    build: formatLocalBuildTime(buildTime),
    commit: sha && sha !== 'unknown' ? sha : '—',
  });

  return (
    <span
      className="hidden sm:inline font-mono text-[11px] leading-none px-2 py-1
                 rounded-md bg-gray-50 text-gray-500 border border-gray-200
                 select-none tracking-tight"
      title={tooltip}
    >
      {label}
    </span>
  );
}
