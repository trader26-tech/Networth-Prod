import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { getStrategyById, StrategyMeta } from '../../shared/strategy-registry';

@Component({
  selector: 'app-short-strangle',
  standalone: true,
  imports: [CommonModule, RouterLink],
  templateUrl: './short-strangle.html',
  styleUrl: './short-strangle.scss',
})
export class ShortStrangleComponent {
  meta: StrategyMeta | undefined = getStrategyById('short-strangle');
}
