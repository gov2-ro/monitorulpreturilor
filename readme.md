# monitorulpreturilor.info

Fetch data from monitorulpreturilor.info api

> Proiectul *Monitorul Prețurilor* produselor alimentare își propune să acorde consumatorilor posibilitatea de a compara prețul aferent coșului de produse a cărui achiziție intenționează să o realizeze.


## Roadmap
- [x] figure out api
- [ ] create fetching scripts
- [ ] store to db
- [ ] automated fetching
    - [ ] make list of relevnt products? - fetch those more often?
- [ ] UI
    - [ ] monitor price variations


### Questions
- same network/shop has different prices for different stores?

## API endpoints

https://monitorulpreturilor.info/pmonsvc/Retail/GetRetailNetworks
https://monitorulpreturilor.info/pmonsvc/Retail/GetUATByName
https://monitorulpreturilor.info/pmonsvc/Retail/GetUATByName?uatname={uatName}
https://monitorulpreturilor.info/pmonsvc/Retail/GetProductCategoriesNetwork
https://monitorulpreturilor.info/pmonsvc/Retail/GetProductCategoriesNetworkOUG
https://monitorulpreturilor.info/pmonsvc/Retail/GetCatalogProductsByNameNetwork?prodname={search}
https://monitorulpreturilor.info/pmonsvc/Retail/GetCatalogProductsById?csvcatprodids={?}
https://monitorulpreturilor.info/pmonsvc/Retail/GetStoresForProductsByLatLon?lat={lat}}&lon={long}&buffer={?}&csvprodids={ids}&OrderBy=price


### Sample urls

see responses in [docs/reference](docs/reference/sampleResponses/)

- https://monitorulpreturilor.info/pmonsvc/Retail/GetCatalogProductsByNameNetwork?CSVcategids=127
- https://monitorulpreturilor.info/pmonsvc/Retail/GetStoresForProductsByLatLon?lat=45.65445813094587&lon=25.64496517181396&buffer=2300&csvprodids=1418315,1268286,1023523,1044471,1026063,1341915&OrderBy=price
- https://monitorulpreturilor.info/pmonsvc/Retail/GetCatalogProductsByNameNetwork?prodname=cafea
- https://monitorulpreturilor.info/pmonsvc/Retail/GetCatalogProductsById?csvcatprodids=1028135



## Notes

- Use this env: ` ~/devbox/envs/240826
- Use `npx playwright` (Playwright already installed) when needed to test or debug the final results.`