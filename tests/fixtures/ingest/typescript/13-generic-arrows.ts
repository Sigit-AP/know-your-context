// Generic arrow components and satisfies-style constants.
type Props<T> = { items: T[]; render: (item: T) => string };

function List<T>(props: Props<T>): string {
  return props.items.map(props.render).join(", ");
}

const config = { strict: true, level: 2 } as const;

export function boot(): string {
  return List({ items: [1, 2], render: String });
}
