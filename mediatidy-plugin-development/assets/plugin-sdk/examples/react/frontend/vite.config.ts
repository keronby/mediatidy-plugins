import {defineConfig} from 'vite'
import react from '@vitejs/plugin-react'
import {federation} from '@module-federation/vite'
export default defineConfig({base:'./',plugins:[react(),federation({name:'mt_react_example',filename:'remoteEntry.js',exposes:{'./Page':'./src/Page.tsx'},shared:Object.fromEntries(['react','react-dom','react/jsx-runtime','react/jsx-dev-runtime','@mediatidy/plugin-ui'].map(name=>[name,{singleton:true,import:false,requiredVersion:name==='@mediatidy/plugin-ui'?'2.0.0':'19.2.8'}]))})],build:{outDir:'../ui',emptyOutDir:true,target:'es2022',rollupOptions:{input:'src/Page.tsx'}}})
