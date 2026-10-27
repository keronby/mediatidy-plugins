import type * as React from 'react'
export interface FileSelection {rootId:string;path:string}
export interface PluginHost {
 instance:{id:string;name:string;build:string;status:string;enabled:boolean;config:Record<string,unknown>}
 theme:string
 language:string
 size:'compact'|'normal'
 runAction:(action:string,input?:Record<string,unknown>,selections?:FileSelection[])=>Promise<unknown>
 query:(name:string,input?:Record<string,unknown>)=>Promise<unknown>
 saveConfig:(config:Record<string,unknown>)=>Promise<void>
 selectFiles:()=>Promise<FileSelection[]>
 selectProfile:()=>Promise<number|null>
 previewUrl:(ref:string)=>Promise<string>
 openLogs:()=>void
 openTasks:()=>void
 openHistory:()=>void
 reviewDrafts:()=>void
 refreshPage:()=>void
}
export interface PluginPageProps {mt:PluginHost}
export function useMT():PluginHost
export const reactIdentity:typeof React.useState
export const Button:React.FC<React.ButtonHTMLAttributes<HTMLButtonElement>&{variant?:'default'|'outline'|'secondary'|'ghost'|'destructive';size?:'default'|'sm'|'lg'|'icon'}>
export const Card:React.FC<React.HTMLAttributes<HTMLDivElement>>
export const CardHeader:typeof Card
export const CardTitle:typeof Card
export const CardContent:typeof Card
export const Input:React.FC<React.InputHTMLAttributes<HTMLInputElement>>
export const Label:React.FC<React.LabelHTMLAttributes<HTMLLabelElement>>
export const Textarea:React.FC<React.TextareaHTMLAttributes<HTMLTextAreaElement>>
export const Progress:React.FC<{value?:number;className?:string}>
