import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/** The one class composer vendored shadcn components import as "@/lib/utils". */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
