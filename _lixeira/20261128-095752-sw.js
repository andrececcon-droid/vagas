self.addEventListener("install",function(){self.skipWaiting()});self.addEventListener("activate",function(e){e.waitUntil(clients.claim())});self.addEventListener("fetch",function(){});
