// Type aliases, unions, literals and mapped shapes.
type Id = string | number;
type Callback = (err: Error | null, value?: string) => void;
type Direction = "north" | "south" | "east" | "west";

enum Colour {
  Red,
  Green,
  Blue,
}

const DEFAULT_DIRECTION: Direction = "north";

function turn(d: Direction): Direction {
  return d === "north" ? "east" : "north";
}
