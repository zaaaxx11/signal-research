# Finviz maps — raw source excerpts

Minified, quoted verbatim as evidence for `finviz-maps-deepdive.md`.

## canvas element (5716)

```js
ender(){const{initialized:e}=this.state,{alt:t,treemap:a,camera:r,legend:n,hover:s,flash:o,onFlashEnd:i}=this.props,c=s||this.isZoomEnabled;return(0,H.jsxs)("div",{id:"canvas-wrapper",className:"custom-scrollbar is-small",children:[(0,H.jsx)("canvas",{ref:this._canvas,className:d()("chart",{initialized:e}),onDoubleClick:c?void 0:this._onDoubleClick,children:t}),c&&(0,H.jsx)("canvas",{ref:this._hoverCanvas,className:"hover-canvas absolute top-0 left-0",onMouseDown:this._onMouseDown,onMouseMove:this._onMouseMove,onMouseUp:this._onMouseUp,onMouseLeave:this._onMouseLeave,onDoubleClick:this._onDoubleClick}),o&&i&&e&&(0,H.jsx)(K,{treemap:a,region:o.region,view:this._getCompositeView(),level:r.getNearestLevel(),hideHeaderArrows:this.props.hideHeaderArrows,onEnd:i},o.id),n&&(0,H.jsx)(X.A,{treemap:a,className:"map-legend pt-4",leading:this.props.legendLeading,scrollable:this.props.scrollableLegend},a.scale.id),s&&(0,H.jsx)(Q,{treemap:a
```

## hover handler (5716)

```js
_onMouseMove(e){if(this.mouseDown||!this.props.hover)return;const{treemap:t}=this.props;var{offsetX:a,offsetY:r}=(0,y.A)(e);const d=this._getCompositeView();let n=t.getNodeAtPosition(a,r,d);if(!n){const e=t.getIndustryAtPosition(a,r,d);n=e?.children[0]}E.A.setHoveredNode(t.mapNodeId,n)}_onMouseUp(){this.mouseDown=!1}_onMouseLeave(){E.A.setHoveredNode(this.props.treemap.mapNodeId,void 0)}_onDoubleClick(e){const{treemap:t}=this.props,{offsetX:a,offsetY:r}=(0,y.A)(e),d=this._getCompositeView(),n=t.getNodeAtPosition(a,r,d),s=t.getIndustryAtPosition(a,r,d),o=t.getSectorAtPosition(a,r,d);this.props.onNodeClick?.({event
```

## zoom + drill (5716)

```js
_onWheel(e){if(this.isZoomEnabled&&(e.preventDefault(),this.state.initialized)){var{offsetX:t,offsetY:a}=(0,y.A)(e);E.A.zoom(this.props.camera,-e.deltaY,t,a)}}_onPanStart(e){this.state.initialized&&(this.lastPanX=e.pointers[0].clientX,this.lastPanY=e.pointers[0].clientY)}_onPanMove(e){if(this.state.initialized){var t=this.lastPanX-e.pointers[0].clientX,a=this.lastPanY-e.pointers[0].clientY;this.lastPanX=e.pointers[0].clientX,this.lastPanY=e.pointers[0].clientY,E.A.changeTranslate(this.props.camera,t,a)}}_onPinch(e)
```

## tile paint (5716)

```js
function S(e,t,a){const r=1/(arguments.length>3&&void 0!==arguments[3]?arguments[3]:1);e.fillStyle=t.colorScale(t.getPerf(a)),e.fillRect(a.x,a.y,a.dx-r,a.dy-r)}function w(e,t,a,r){let d=arguments.length>4&&void 0!==arguments[4]?arguments[4]:{};const{isHovered:n,getSpriteImage:s}=d;if(n&&!s)return;const o=t.getNodeText(
```

## bootstrap (map.v1)

```js
window.FinvizInitCanvas=function(e){window.FinvizInitCanvas=()=>{0};const t=document.getElementById("root");(0,Oe.b3)();r.createRoot(t).render((0,Y.jsx)(He.w,{children:(0,Y.jsx)(Ze,{...e})}))},window.dispatchEvent(new Event("Finviz:FinvizInitCanvas"));n(84847)},45049(e,t,n){"use strict";n.d(t,{Ak:()=>h,F4:()=>m,TU:()=>f,s9:()=>d,yf:()=>p});n(14846),n(63333),n(27458),n(94364),n(5673),n(30164),n(21279);var r=n(58066),a
```

## squarify (152)

```js
=function(t){return arguments.length?(c="function"==typeof t?t:ae(+t),l):c},l.paddingLeft=function(t){return arguments.length?(s="function"==typeof t?t:ae(+t),l):s},l}const Xe=function t(n){function e(t,e,r,i,o){if((u=t._squarify)&&u.ratio===n)for(var u,a,c,s,l,f=-1,h=u.length,p=t.value;++f<h;){for(c=(a=u[f]).children,s=a.value=0,l=c.length;s<l;++s)a.value+=c[s].value;a.dice?Pe(a,e,r,i,p?r+=(o-r)*a.value/p:o):Ne(a,e,r,p?e+=(i-e)*a.value/p:i,o),p-=a.value}else t._squarify=u=Ce(n,t,e,r,i,o),u.ratio=n}return e.ratio=function(n){return t((n=+n)>1?n:1)},e}(Fe);var Re=e(95087);function Ie(t){return"string"==typeof t?new St([[document.querySelector(t)]],[document.documentElement]):new St([[t]],Tt)}var Ve=e(22896),Ye=e(24322),De=e(21647);
```

