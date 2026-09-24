// Minimal Shadcn/ui-style Button: same API shape (variant/size props,
// forwardRef, cva) without pulling in the full shadcn CLI/registry.
import { cva, type VariantProps } from "class-variance-authority";
import { forwardRef, type ButtonHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 rounded-md text-sm font-medium transition-colors disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        default: "bg-ink-primary text-surface-primary hover:opacity-90",
        outline:
          "border border-border bg-transparent hover:bg-surface-secondary text-ink-primary",
        ghost: "hover:bg-surface-secondary text-ink-primary",
        destructive: "bg-status-rejected text-white hover:opacity-90",
      },
      size: {
        default: "h-9 px-4 py-2",
        // 44px minimum on phones (tap-target guideline); back to the original
        // compact size from the sm: breakpoint up, where a mouse is precise.
        sm: "h-11 px-4 text-xs sm:h-8 sm:px-3",
        icon: "h-11 w-11 sm:h-9 sm:w-9",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  }
);

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, ...props }, ref) => (
    <button
      ref={ref}
      className={cn(buttonVariants({ variant, size }), className)}
      {...props}
    />
  )
);
Button.displayName = "Button";
