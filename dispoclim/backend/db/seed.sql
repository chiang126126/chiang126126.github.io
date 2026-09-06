-- Seed minimal : produits, enseignes et quelques magasins (sous-ensemble du front).
-- Permet de lancer la chaîne complète avec le connecteur 'demo'.

insert into products (id, brand, name, short, btu, kw, energy, msrp) values
 ('midea-portasplit-12000','Midea',$$Midea PortaSplit Mobile Silent 4-en-1$$,'PortaSplit 12000 BTU',12000,3.5,'A++',799),
 ('comfee-9000','COMFEE''',$$COMFEE' Mobile Air Conditioner 9000 BTU/h$$,'COMFEE 9000 BTU',9000,2.6,'A',299),
 ('midea-portasplit-cool','Midea','Midea PortaSplit Cool','PortaSplit Cool',10000,2.9,'A+',649),
 ('support-universel','Midea','Support mural universel PortaSplit','Support universel',null,null,null,49)
on conflict (id) do nothing;

insert into retailers (id, name, color, kind) values
 ('leroy-merlin','Leroy Merlin','#78be20','magasin'),
 ('castorama','Castorama','#0a4ea2','magasin'),
 ('boulanger','Boulanger','#e2001a','magasin'),
 ('darty','Darty','#e2001a','magasin'),
 ('weldom','Weldom','#e74011','magasin'),
 ('bricoman','Bricoman','#005ca9','magasin'),
 ('manomano','ManoMano','#5c2d91','en ligne'),
 ('amazon-fr','Amazon.fr','#ff9900','en ligne')
on conflict (id) do nothing;

insert into stores (id, retailer_id, name, city, cp, lat, lon, online, poll_tier) values
 ('lm-annemasse','leroy-merlin','Leroy Merlin Annemasse','Ville-la-Grand','74100',46.207,6.252,false,'hot'),
 ('casto-annecy','castorama','Castorama Annecy','Seynod','74600',45.879,6.085,false,'hot'),
 ('bou-annemasse','boulanger','Boulanger Annemasse','Étrembières','74100',46.176,6.225,false,'hot'),
 ('darty-annecy','darty','Darty Annecy','Épagny','74330',45.943,6.092,false,'warm'),
 ('weldom-saint-julien','weldom','Weldom Saint-Julien-en-Genevois','Saint-Julien','74160',46.143,6.083,false,'warm'),
 ('bric-annecy','bricoman','Bricoman Annecy','Metz-Tessy','74370',45.945,6.105,false,'warm'),
 ('lm-lyon-est','leroy-merlin','Leroy Merlin Lyon Est','Saint-Priest','69800',45.700,4.944,false,'warm'),
 ('bou-lyon','boulanger','Boulanger Lyon La Part-Dieu','Lyon','69003',45.760,4.857,false,'warm'),
 ('lm-ivry','leroy-merlin','Leroy Merlin Ivry','Ivry-sur-Seine','94200',48.813,2.391,false,'warm'),
 ('bou-madeleine','boulanger','Boulanger Paris Madeleine','Paris','75008',48.870,2.324,false,'warm'),
 ('manomano-fr','manomano','ManoMano (livraison France)','En ligne','00000',null,null,true,'hot'),
 ('amazon-fr-store','amazon-fr','Amazon.fr (livraison France)','En ligne','00000',null,null,true,'hot')
on conflict (id) do nothing;
