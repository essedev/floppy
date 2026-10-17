/* A disk's state as an LED and a readout: being created, failed, or reachable or not. */

import { State } from '@/components/detail'
import { useI18n } from '@/i18n'
import type { Disk } from '@/lib/api'

/** The state of one disk. */
export function DiskState({ disk }: { disk: Disk }) {
  const { t } = useI18n()
  if (disk.state === 'creating') return <State led="dl" blink>{t.disks.creating}</State>
  if (disk.state === 'failed') return <State led="err">{t.disks.failedState}</State>
  return <State led={disk.status === 'online' ? 'ok' : 'err'}>{disk.status === 'online' ? t.overview.online : t.overview.unreachable}</State>
}
