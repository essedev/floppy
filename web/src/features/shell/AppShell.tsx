/*
  The signed-in layout (concept-e-admin, DECISIONS #12): a left sidebar with the wordmark, the
  organization, the sections and the person; the page on the right. Members see only their
  own devices. On a narrow screen the sidebar folds into a bar on top.
*/

import { FolderLock, HardDrive, KeyRound, LaptopMinimal, LayoutGrid, LogOut, Monitor, Moon, ScrollText, SlidersHorizontal, Sun, Users, type LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router'
import { IconButton } from '@/components/Button'
import { Avatar, Led } from '@/components/detail'
import { Wordmark } from '@/components/marks'
import { useI18n, plural } from '@/i18n'
import { useTheme, type ThemeMode } from '@/lib/theme'
import { useCurrentSession, useSignOut } from '@/features/auth/hooks'
import { openWindows, useKeyStatus } from '@/features/key/hooks'
import { FakeBanner } from './FakeBanner'

const NEXT_THEME: Record<ThemeMode, ThemeMode> = { system: 'light', light: 'dark', dark: 'system' }
const THEME_ICON: Record<ThemeMode, LucideIcon> = { system: Monitor, light: Sun, dark: Moon }

function NavItem({ to, icon: Icon, label, extra, end }: { to: string; icon: LucideIcon; label: string; extra?: ReactNode; end?: boolean }) {
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) =>
        `flex h-[38px] shrink-0 items-center gap-[11px] rounded-[10px] px-2.5 text-sm whitespace-nowrap no-underline ${isActive ? 'bg-raised font-semibold text-ink' : 'text-ink-2 hover:bg-raised/60 hover:text-ink'}`
      }
    >
      <Icon size={18} strokeWidth={1.8} aria-hidden />
      <span>{label}</span>
      {extra && <span className="ml-auto pl-2 max-md:hidden">{extra}</span>}
    </NavLink>
  )
}

function KeyNavLed() {
  const { t } = useI18n()
  const key = useKeyStatus()
  return openWindows(key.data).length > 0 ? <Led state="dl" label={t.nav.keyWindowOpen} /> : null
}

/** The layout of every signed-in page. */
export function AppShell() {
  const { t, lang, locale, setLang } = useI18n()
  const session = useCurrentSession()
  const signOut = useSignOut()
  const navigate = useNavigate()
  const [theme, setTheme] = useTheme()
  const admin = session.role === 'admin'
  const org = session.organization
  const count = (n: number) => <span className="num text-xs text-ink-3">{n}</span>
  const ThemeIcon = THEME_ICON[theme]

  return (
    <div className="flex min-h-dvh flex-col">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:rounded-lg focus:bg-raised focus:px-3 focus:py-2">
        {t.common.skip}
      </a>
      <FakeBanner />
      <div className="grid flex-1 md:grid-cols-[232px_minmax(0,1fr)]">
        <aside className="flex min-w-0 flex-col gap-0.5 bg-sunken px-3 py-[18px] md:sticky md:top-0 md:h-dvh max-md:gap-3 max-md:py-3">
          <div className="flex items-center justify-between gap-3 px-2 md:mb-[18px] md:mt-0.5">
            <Wordmark />
            <span className="text-[13px] font-semibold md:hidden">{org.name}</span>
          </div>
          <div className="mb-3.5 rounded-xl bg-raised px-3 py-2.5 max-md:hidden">
            <div className="text-sm font-semibold">{org.name}</div>
            {admin && (
              <div className="text-xs text-ink-3">
                {plural(t.nav.orgDisks, org.diskCount, locale)} · {plural(t.nav.orgPeople, org.peopleCount, locale)}
              </div>
            )}
          </div>
          <nav aria-label={t.nav.label} className="flex gap-0.5 md:flex-col max-md:-mx-3 max-md:overflow-x-auto max-md:px-3">
            {admin ? (
              <>
                <NavItem to="/" end icon={LayoutGrid} label={t.nav.overview} />
                <NavItem to="/persone" icon={Users} label={t.nav.people} extra={count(org.peopleCount)} />
                <NavItem to="/accessi" icon={FolderLock} label={t.nav.access} />
                <NavItem to="/dispositivi" icon={LaptopMinimal} label={t.nav.devices} extra={count(org.deviceCount)} />
                <NavItem to="/dischi" icon={HardDrive} label={t.nav.disks} />
                <NavItem to="/chiave" icon={KeyRound} label={t.nav.key} extra={<KeyNavLed />} />
                <NavItem to="/registro" icon={ScrollText} label={t.nav.audit} />
                <NavItem to="/impostazioni" icon={SlidersHorizontal} label={t.nav.settings} />
              </>
            ) : (
              <NavItem to="/dispositivi" icon={LaptopMinimal} label={t.nav.myDevices} />
            )}
          </nav>
          <div className="mt-auto flex items-center gap-2.5 px-2.5 pt-3 max-md:hidden">
            <Avatar name={session.user.name} size={30} />
            <div className="min-w-0 flex-1 text-[13px] leading-tight">
              <div className="truncate">{session.user.name}</div>
              <div className="text-xs text-ink-3">{t.roles[session.role]}</div>
            </div>
          </div>
          <div className="flex items-center gap-0.5 px-1.5 pt-2 max-md:absolute max-md:top-3 max-md:right-3 max-md:hidden">
            <IconButton label={`${t.shell.theme[theme]}. ${t.shell.theme.next}`} onClick={() => setTheme(NEXT_THEME[theme])}>
              <ThemeIcon size={17} strokeWidth={1.8} aria-hidden />
            </IconButton>
            <button
              type="button"
              lang={lang === 'it' ? 'en' : 'it'}
              aria-label={t.lang.switchLabel}
              title={t.lang.switchLabel}
              onClick={() => setLang(lang === 'it' ? 'en' : 'it')}
              className="readout h-[34px] rounded-[9px] border-0 bg-transparent px-2 hover:bg-raised"
            >
              {lang === 'it' ? 'EN' : 'IT'}
            </button>
            <IconButton
              label={t.shell.signOut}
              className="ml-auto"
              disabled={signOut.isPending}
              onClick={() => signOut.mutate(undefined, { onSuccess: () => navigate('/accedi', { replace: true }) })}
            >
              <LogOut size={17} strokeWidth={1.8} aria-hidden />
            </IconButton>
          </div>
        </aside>
        <main id="main" className="min-w-0 px-[34px] pt-7 pb-10 max-md:px-4 max-md:pt-5">
          <Outlet />
          <div className="mt-10 flex items-center gap-1 border-t border-line pt-3 md:hidden">
            <IconButton label={`${t.shell.theme[theme]}. ${t.shell.theme.next}`} onClick={() => setTheme(NEXT_THEME[theme])}>
              <ThemeIcon size={17} strokeWidth={1.8} aria-hidden />
            </IconButton>
            <button type="button" aria-label={t.lang.switchLabel} onClick={() => setLang(lang === 'it' ? 'en' : 'it')} className="readout h-[34px] rounded-[9px] border-0 bg-transparent px-2 hover:bg-sunken">
              {lang === 'it' ? 'EN' : 'IT'}
            </button>
            <span className="ml-auto truncate text-[13px] text-ink-2">{session.user.name}</span>
            <IconButton label={t.shell.signOut} onClick={() => signOut.mutate(undefined, { onSuccess: () => navigate('/accedi', { replace: true }) })}>
              <LogOut size={17} strokeWidth={1.8} aria-hidden />
            </IconButton>
          </div>
        </main>
      </div>
    </div>
  )
}
