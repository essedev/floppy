/* A bucket's versioning as the disks and keys pages show it. */

import { fmt, useI18n } from '@/i18n'
import type { Versioning } from '@/lib/api'
import { State } from './detail'

/** A bucket's versioning as an LED and a readout: on, off or suspended (advised against), not read. */
export function VersioningState({ versioning }: { versioning: Versioning }) {
  const { t } = useI18n()
  const v = versioning
  if (v.state === 'enabled') {
    return <State led="ok">{v.oldVersionsDays ? fmt(t.disks.versioningDays, { n: v.oldVersionsDays }) : t.disks.versioningValue.enabled}</State>
  }
  return <State led={v.state === 'unknown' ? 'off' : 'dl'}>{t.disks.versioningValue[v.state]}</State>
}
