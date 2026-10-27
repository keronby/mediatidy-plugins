import {useEffect,useState} from 'react'
import {Panel,PanelGroup,PanelResizeHandle} from 'react-resizable-panels'
import {Button,Card,CardContent,useMT,reactIdentity,type PluginPageProps} from '@mediatidy/plugin-ui'
import styles from './Page.module.css'
export default function Page({mt}:PluginPageProps){
 const host=useMT(),[count,setCount]=useState(0),[visible,setVisible]=useState(!document.hidden)
 if(reactIdentity!==useState||host!==mt)throw Error('React/shared UI identity mismatch')
 useEffect(()=>{const onChange=()=>setVisible(!document.hidden);document.addEventListener('visibilitychange',onChange);return()=>document.removeEventListener('visibilitychange',onChange)},[])
 useEffect(()=>{let closed=false;void mt.query('history').then(value=>{if(!closed)setCount((value as {count:number}).count)});return()=>{closed=true}},[mt.instance.build])
 return <div className={styles.root} data-react-singleton="verified"><Card><CardContent><h2>原生 React 组件 · 第三方可调面板</h2><p>{mt.theme} · {mt.language} · {mt.size} · {visible?'页面可见':'页面隐藏'}</p><PanelGroup direction={mt.size==='compact'?'vertical':'horizontal'} className={styles.panels}><Panel defaultSize={50}><div className={styles.panel}><p>计数：{count}</p><Button disabled={!mt.instance.enabled} onClick={async()=>{await mt.runAction('increment');const next=await mt.query('history') as {count:number};setCount(next.count)}}>运行 Python 动作</Button></div></Panel><PanelResizeHandle className={styles.handle}/><Panel><div className={styles.panel}><p>拖动分隔条调整布局。组件由 react-resizable-panels 提供。</p><Button variant="outline" onClick={mt.openLogs}>查看日志</Button></div></Panel></PanelGroup></CardContent></Card></div>
}
