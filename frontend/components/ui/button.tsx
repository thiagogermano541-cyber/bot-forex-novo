import * as React from "react";
import {Slot} from "@radix-ui/react-slot";
import {cva, type VariantProps} from "class-variance-authority";
import {cn} from "@/lib/utils";
const variants=cva("inline-flex items-center justify-center gap-2 rounded-lg text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-400 disabled:pointer-events-none disabled:opacity-40",{variants:{variant:{default:"bg-violet-500 text-white hover:bg-violet-400",outline:"border border-border bg-transparent hover:bg-muted",ghost:"hover:bg-muted"},size:{default:"h-10 px-4",sm:"h-8 px-3"}},defaultVariants:{variant:"default",size:"default"}});
export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>,VariantProps<typeof variants>{asChild?:boolean}
export const Button=React.forwardRef<HTMLButtonElement,ButtonProps>(({className,variant,size,asChild=false,...props},ref)=>{const Comp=asChild?Slot:"button";return <Comp className={cn(variants({variant,size,className}))} ref={ref} {...props}/>});
Button.displayName="Button";
