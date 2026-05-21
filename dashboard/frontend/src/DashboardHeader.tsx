import { TeamSyncStatusPill } from "./TeamSyncStatusPill";

interface DashboardHeaderProps {
  /** Project-name slot. Optional — omitted until a project source wires it. */
  projectName?: string;
}

export function DashboardHeader({ projectName }: DashboardHeaderProps) {
  return (
    <header class="header">
      <span class="header-wordmark">Bicameral</span>
      {projectName ? (
        <span class="header-project">{projectName}</span>
      ) : null}
      <span class="header-spacer" />
      <TeamSyncStatusPill />
    </header>
  );
}
