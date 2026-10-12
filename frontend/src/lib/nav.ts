export interface NavItem {
  path: string
  label: string
  icon: string // SVG path data (24x24, stroke)
  description: string
}

export const NAV_ITEMS: NavItem[] = [
  {
    path: '/overview',
    label: 'Overview',
    icon: 'M3 12l9-9 9 9M5 10v10h14V10',
    description: 'Month-to-date spend, forecast, savings potential and alerts at a glance.',
  },
  {
    path: '/costs',
    label: 'Costs',
    icon: 'M4 19V9m6 10V5m6 14v-7m4 7H2',
    description: 'Daily cost trend and breakdown by service, region and tag.',
  },
  {
    path: '/resources',
    label: 'Resources',
    icon: 'M4 6h16M4 12h16M4 18h16',
    description: 'Inventory of EC2, EBS, snapshots, Elastic IPs, RDS, S3 and Lambda.',
  },
  {
    path: '/recommendations',
    label: 'Recommendations',
    icon: 'M12 3a6 6 0 00-3 11.2V17h6v-2.8A6 6 0 0012 3zM9 21h6',
    description: 'Waste findings with estimated monthly savings.',
  },
  {
    path: '/budgets',
    label: 'Budgets & Alerts',
    icon: 'M15 17h5l-1.4-1.4A2 2 0 0118 14.2V11a6 6 0 10-12 0v3.2a2 2 0 01-.6 1.4L4 17h5m6 0a3 3 0 11-6 0',
    description: 'Monthly budget, anomaly detection and alert history.',
  },
  {
    path: '/tags',
    label: 'Tags',
    icon: 'M7 7h.01M3 3h8l10 10-8 8L3 11V3z',
    description: 'Tag compliance and cost by tag value.',
  },
  {
    path: '/settings',
    label: 'Settings',
    icon: 'M12 15a3 3 0 100-6 3 3 0 000 6zm7.4-3a7.4 7.4 0 00-.1-1.2l2-1.6-2-3.4-2.4 1a7.5 7.5 0 00-2-1.2L14.5 3h-5l-.4 2.6a7.5 7.5 0 00-2 1.2l-2.4-1-2 3.4 2 1.6a7.4 7.4 0 000 2.4l-2 1.6 2 3.4 2.4-1a7.5 7.5 0 002 1.2l.4 2.6h5l.4-2.6a7.5 7.5 0 002-1.2l2.4 1 2-3.4-2-1.6c.1-.4.1-.8.1-1.2z',
    description: 'Connected AWS accounts, rule thresholds and notification channels.',
  },
]
