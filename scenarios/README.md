# Benzetim Senaryoları

Bu dizin G3 sırasında normal start/shutdown, eksik PGOOD, gecikmiş rail, load step, input sag/brownout, reset ve back-power risk senaryoları için ayrılmıştır. Fiziksel model veya G3 sonucu henüz yoktur.

WF-02-03 yazılım kabulü için kullanılan normal startup, yanlış sıra, timeout, eksik PGOOD, tekrar, bilinmeyen pencere ve power-off fixture'ları `tests/fixtures/sequencing` altındadır. Bunlar yalnız sentetik kural testidir; bu dizindeki gelecekteki simülasyon veya laboratuvar kanıtının yerine geçmez.

WF-02-04 yazılım kabulü için kullanılan brownout, stuck-low, delay, rail-loss, restart, recovery kombinasyonu, fault sırasında restart ve bilinmeyen threshold fixture'ları `tests/fixtures/fault_injection` altındadır. Bunlar deterministik kural girdileridir; dalga biçimi, transient simülasyon veya fiziksel arıza enjeksiyonu değildir.
