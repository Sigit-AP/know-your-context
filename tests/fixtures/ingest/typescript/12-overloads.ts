// Function overload signatures plus one implementation.
function pad(value: string, width: number): string;
function pad(value: number, width: number): string;
function pad(value: string | number, width: number): string {
  return String(value).padStart(width);
}

declare function external(id: number): Promise<string>;

class Converter {
  convert(value: string): number;
  convert(value: number): string;
  convert(value: string | number): string | number {
    return typeof value === "string" ? Number(value) : String(value);
  }
}
