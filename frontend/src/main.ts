import { bootstrapApplication } from '@angular/platform-browser';
import { provideAnimations } from '@angular/platform-browser/animations';
import { provideHttpClient } from '@angular/common/http';
import { provideRouter } from '@angular/router';
import { ModuleRegistry, AllCommunityModule } from 'ag-grid-community';
import { AppComponent } from './app/app.component';

ModuleRegistry.registerModules([AllCommunityModule]);

bootstrapApplication(AppComponent, {
  providers: [provideAnimations(), provideHttpClient(), provideRouter([])]
}).catch(error => console.error(error));
