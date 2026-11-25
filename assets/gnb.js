// 나도랩스 공통 머리말: "앱" 메뉴 열고 닫기, 지금 페이지 표시, (있으면) 화면 테마 버튼
(function(){
  var box=document.querySelector('.gnb-apps'); if(!box) return;
  var btn=box.querySelector('button'), menu=box.querySelector('.gnb-menu');
  function set(open){btn.setAttribute('aria-expanded',open?'true':'false'); menu.hidden=!open;}
  btn.addEventListener('click',function(e){e.stopPropagation(); set(menu.hidden); if(!menu.hidden){var f=menu.querySelector('a'); if(f&&e.detail===0) f.focus();}});
  document.addEventListener('click',function(e){if(!menu.hidden&&!box.contains(e.target)) set(false);});
  document.addEventListener('keydown',function(e){if(e.key==='Escape'&&!menu.hidden){set(false); btn.focus();}});
  box.addEventListener('focusout',function(e){if(e.relatedTarget&&!box.contains(e.relatedTarget)) set(false);});
  // 지금 보고 있는 앱 표시
  var here=location.pathname;
  [].forEach.call(menu.querySelectorAll('a[href^="/"]'),function(a){
    var p=a.getAttribute('href'); if(p!=='/#apps'&&here.indexOf(p)===0){a.setAttribute('aria-current','page'); box.classList.add('cur');}
  });
  // 메뉴 안 링크를 누르면 닫는다(같은 페이지 안 이동 대비)
  menu.addEventListener('click',function(e){if(e.target.closest('a')) set(false);});
  // 테마 버튼(.gnb-theme)이 있는 페이지만
  var tb=document.querySelector('.gnb-theme');
  if(tb){var root=document.documentElement; tb.addEventListener('click',function(){
    var dark=root.dataset.theme? root.dataset.theme==='dark' : matchMedia('(prefers-color-scheme: dark)').matches;
    root.dataset.theme=dark?'light':'dark'; try{localStorage.setItem('theme',root.dataset.theme);}catch(e){}
  });}
})();
