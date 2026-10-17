/* Parsing the numbers of the disk forms. */

/** A whole number in a range parsed from a field, or null. */
export const intIn = (value: string, min: number, max: number) => {
  const n = Number(value)
  return Number.isInteger(n) && n >= min && n <= max ? n : null
}
